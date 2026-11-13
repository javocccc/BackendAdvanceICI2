from collections.abc import Mapping
from decimal import Decimal
import unicodedata

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import Acta, CambioColegiatura, ConfiguracionSistema, DocumentoExpediente, Expediente, PerfilUsuario, RegistroAuditoria
from .rut import normalizar_rut


class CamposJsonEsperadosMixin:
    """Evita aceptar en silencio claves ajenas al contrato JSON de la API."""

    def to_internal_value(self, data):
        request = self.context.get("request")
        if request and request.content_type.split(";", 1)[0].lower() == "application/json" and isinstance(data, Mapping):
            escribibles = {campo.field_name for campo in self._writable_fields}
            extras = set(data) - escribibles
            if extras:
                raise serializers.ValidationError({
                    nombre: ["Campo no admitido por este endpoint."] for nombre in sorted(extras)
                })
        return super().to_internal_value(data)


def validar_nombre_persona(valor):
    # La regla se comparte entre el estudiante y los integrantes de la comisión.
    signos = {" ", "-", "'", "’", "."}
    if not any(letra.isalpha() for letra in valor) or any(
        not (letra.isalpha() or letra in signos) for letra in valor
    ):
        raise serializers.ValidationError(
            "El nombre solo puede contener letras, espacios, guiones, apóstrofos y puntos."
        )
    return valor


class PerfilUsuarioSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="usuario.email", read_only=True)
    expediente_id = serializers.IntegerField(source="expediente.id", read_only=True)

    class Meta:
        model = PerfilUsuario
        fields = ["id", "email", "nombre", "rol", "activo", "expediente_id"]


class ExpedienteSerializer(CamposJsonEsperadosMixin, serializers.ModelSerializer):
    estudiante = PerfilUsuarioSerializer(source="perfil_estudiante", read_only=True)
    creado_por = serializers.CharField(source="creado_por.username", read_only=True)
    acta_id = serializers.IntegerField(source="acta.id", read_only=True)
    # Se normaliza antes de comprobar el formato canónico y la unicidad.
    rut = serializers.CharField(max_length=12)
    # El formulario EVA1 exige nota y tres docentes desde la creación.
    nota_examen = serializers.DecimalField(
        max_digits=2, decimal_places=1, min_value=Decimal("1.0"), max_value=Decimal("7.0")
    )
    profesor_guia = serializers.CharField(max_length=180, validators=[validar_nombre_persona])
    informante_1 = serializers.CharField(max_length=180, validators=[validar_nombre_persona])
    informante_2 = serializers.CharField(max_length=180, validators=[validar_nombre_persona])
    informante_adicional = serializers.CharField(
        max_length=180, required=False, allow_blank=True, validators=[validar_nombre_persona]
    )

    class Meta:
        model = Expediente
        fields = [
            "id", "nombre", "rut", "anio_egreso", "semestre_egreso", "fecha_examen",
            "nota_examen", "profesor_guia", "informante_1", "informante_2",
            "informante_adicional", "colegiatura", "fecha_colegiatura",
            "estudiante", "acta_id", "creado_por", "fecha_creacion", "fecha_actualizacion",
        ]
        read_only_fields = ["colegiatura", "fecha_colegiatura"]

    def validate_rut(self, valor):
        normalizado = normalizar_rut(valor)
        existentes = Expediente.objects.filter(rut=normalizado)
        if self.instance is not None:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise serializers.ValidationError("Ya existe un expediente con ese RUT.")
        return normalizado

    def validate_nombre(self, valor):
        return validar_nombre_persona(valor)

    def validate(self, attrs):
        # La comisión de examen no puede repetir a un docente en dos puestos.
        campos = ("profesor_guia", "informante_1", "informante_2", "informante_adicional")
        nombres = [attrs.get(campo, getattr(self.instance, campo, "")) for campo in campos]
        presentes = [
            "".join(
                letra for letra in unicodedata.normalize("NFD", " ".join(nombre.split()).casefold())
                if not unicodedata.combining(letra)
            ).strip()
            for nombre in nombres if nombre.strip()
        ]
        if len(presentes) != len(set(presentes)):
            raise serializers.ValidationError("Un docente aparece más de una vez en la comisión.")
        return attrs


class ActaSerializer(serializers.ModelSerializer):
    cargada_por = serializers.CharField(source="cargada_por.username", read_only=True)

    class Meta:
        model = Acta
        fields = ["id", "expediente", "archivo", "nombre_original", "fecha_carga", "cargada_por"]
        read_only_fields = ["nombre_original", "fecha_carga", "cargada_por"]

    def validate_archivo(self, archivo):
        if not archivo.name.lower().endswith(".pdf") or archivo.size == 0 or archivo.size >= 10 * 1024 * 1024:
            raise serializers.ValidationError("El acta debe ser un PDF no vacío de menos de 10 MB.")
        cabecera = archivo.read(5)
        archivo.seek(0)
        if cabecera != b"%PDF-":
            raise serializers.ValidationError("El archivo no tiene una cabecera PDF válida.")
        return archivo

    def validate_expediente(self, expediente):
        existentes = Acta.objects.filter(expediente=expediente)
        if self.instance is not None:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise serializers.ValidationError("Este expediente ya tiene un acta; edite la existente.")
        return expediente

    def create(self, validated_data):
        archivo = validated_data["archivo"]
        return Acta.objects.create(**validated_data, nombre_original=archivo.name)

    def update(self, instance, validated_data):
        if "archivo" in validated_data:
            instance.nombre_original = validated_data["archivo"].name
        return super().update(instance, validated_data)


class DocumentoExpedienteSerializer(serializers.ModelSerializer):
    cargado_por = serializers.CharField(source="cargado_por.username", read_only=True)

    class Meta:
        model = DocumentoExpediente
        fields = ["id", "expediente", "archivo", "nombre_original", "tipo", "tamano", "fecha_carga", "cargado_por"]
        read_only_fields = fields


class CargaDocumentosSerializer(serializers.Serializer):
    # El template permite elegir varios archivos desde la API navegable de DRF.
    archivos = serializers.ListField(
        child=serializers.FileField(), min_length=1, max_length=20,
        style={"template": "gestion/multiple_file_input.html"},
    )
    expedientes = serializers.CharField(
        help_text="IDs de destino separados por comas, uno por archivo y en el mismo orden."
    )


class CargaDocumentosExpedienteSerializer(serializers.Serializer):
    archivos = serializers.ListField(
        child=serializers.FileField(), min_length=1, max_length=20,
        style={"template": "gestion/multiple_file_input.html"},
    )


class CambioColegiaturaSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = CambioColegiatura
        fields = ["id", "estado_anterior", "estado_nuevo", "fecha", "usuario", "observacion"]


class NuevoEstadoColegiaturaSerializer(CamposJsonEsperadosMixin, serializers.Serializer):
    estado_nuevo = serializers.ChoiceField(choices=Expediente.EstadoColegiatura.choices)
    observacion = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class RegistroAuditoriaSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = RegistroAuditoria
        fields = ["id", "usuario", "accion", "detalles", "fecha"]


class CuentaSerializer(CamposJsonEsperadosMixin, serializers.ModelSerializer):
    email = serializers.EmailField(source="usuario.email")
    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})

    class Meta:
        model = PerfilUsuario
        fields = ["id", "email", "password", "nombre", "rol", "activo", "expediente"]

    def validate_email(self, email):
        email = email.strip().lower()
        usuarios = get_user_model().objects.filter(email__iexact=email)
        nombres_usuario = get_user_model().objects.filter(username__iexact=email)
        if self.instance:
            usuarios = usuarios.exclude(pk=self.instance.usuario_id)
            nombres_usuario = nombres_usuario.exclude(pk=self.instance.usuario_id)
        if usuarios.exists() or nombres_usuario.exists():
            raise serializers.ValidationError("Ya existe una cuenta con este correo.")
        return email

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "Indique una contraseña inicial."})
        if attrs.get("password"):
            validate_password(attrs["password"])
        rol = attrs.get("rol", getattr(self.instance, "rol", None))
        expediente = attrs.get("expediente", getattr(self.instance, "expediente", None))
        if rol != PerfilUsuario.Rol.ESTUDIANTE and expediente:
            raise serializers.ValidationError({"expediente": "Solo un estudiante puede vincular un expediente."})
        if expediente and PerfilUsuario.objects.filter(expediente=expediente).exclude(
            pk=self.instance.pk if self.instance else None
        ).exists():
            raise serializers.ValidationError({"expediente": "Este expediente ya está vinculado."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data.pop("usuario")["email"]
        password = validated_data.pop("password")
        usuario = get_user_model().objects.create_user(username=email, email=email, password=password)
        perfil = usuario.perfil
        for campo, valor in validated_data.items():
            setattr(perfil, campo, valor)
        perfil.save()
        return perfil

    @transaction.atomic
    def update(self, instance, validated_data):
        email = validated_data.pop("usuario", {}).get("email")
        password = validated_data.pop("password", None)
        if email is not None:
            instance.usuario.email = email
            instance.usuario.username = email
        if password:
            instance.usuario.set_password(password)
        if email is not None or password:
            instance.usuario.save()
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        return instance


class CambiarClaveSerializer(CamposJsonEsperadosMixin, serializers.Serializer):
    clave_actual = serializers.CharField(style={"input_type": "password"})
    clave_nueva = serializers.CharField(style={"input_type": "password"})

    def validate_clave_nueva(self, valor):
        validate_password(valor, user=self.context["request"].user)
        return valor


class ImportarCuentasSerializer(serializers.Serializer):
    archivo = serializers.FileField()


class ConfiguracionSistemaSerializer(CamposJsonEsperadosMixin, serializers.ModelSerializer):
    class Meta:
        model = ConfiguracionSistema
        fields = ["mantenimiento", "mensaje", "actualizado"]
        read_only_fields = ["actualizado"]

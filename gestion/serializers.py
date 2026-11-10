from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import Acta, CambioColegiatura, ConfiguracionSistema, Expediente, PerfilUsuario, RegistroAuditoria
from .rut import normalizar_rut


class PerfilUsuarioSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="usuario.email", read_only=True)
    expediente_id = serializers.IntegerField(source="expediente.id", read_only=True)

    class Meta:
        model = PerfilUsuario
        fields = ["id", "email", "nombre", "rol", "activo", "expediente_id"]


class ExpedienteSerializer(serializers.ModelSerializer):
    estudiante = PerfilUsuarioSerializer(source="perfil_estudiante", read_only=True)
    creado_por = serializers.CharField(source="creado_por.username", read_only=True)
    acta_id = serializers.IntegerField(source="acta.id", read_only=True)

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
        return normalizar_rut(valor)

    def validate(self, attrs):
        # La comisión de examen no puede repetir a un docente en dos puestos.
        campos = ("profesor_guia", "informante_1", "informante_2", "informante_adicional")
        nombres = [attrs.get(campo, getattr(self.instance, campo, "")) for campo in campos]
        presentes = [nombre.strip().casefold() for nombre in nombres if nombre.strip()]
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
        if self.instance is None and Acta.objects.filter(expediente=expediente).exists():
            raise serializers.ValidationError("Este expediente ya tiene un acta; edite la existente.")
        return expediente

    def create(self, validated_data):
        archivo = validated_data["archivo"]
        return Acta.objects.create(**validated_data, nombre_original=archivo.name)

    def update(self, instance, validated_data):
        if "archivo" in validated_data:
            instance.nombre_original = validated_data["archivo"].name
        return super().update(instance, validated_data)


class CambioColegiaturaSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = CambioColegiatura
        fields = ["id", "estado_anterior", "estado_nuevo", "fecha", "usuario", "observacion"]


class NuevoEstadoColegiaturaSerializer(serializers.Serializer):
    estado_nuevo = serializers.ChoiceField(choices=Expediente.EstadoColegiatura.choices)
    observacion = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class RegistroAuditoriaSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = RegistroAuditoria
        fields = ["id", "usuario", "accion", "detalles", "fecha"]


class CuentaSerializer(serializers.ModelSerializer):
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


class CambiarClaveSerializer(serializers.Serializer):
    clave_actual = serializers.CharField(style={"input_type": "password"})
    clave_nueva = serializers.CharField(style={"input_type": "password"})

    def validate_clave_nueva(self, valor):
        validate_password(valor, user=self.context["request"].user)
        return valor


class ImportarCuentasSerializer(serializers.Serializer):
    archivo = serializers.FileField()


class ConfiguracionSistemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfiguracionSistema
        fields = ["mantenimiento", "mensaje", "actualizado"]
        read_only_fields = ["actualizado"]

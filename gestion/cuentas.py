import csv
import io
import secrets

from django.contrib.auth import update_session_auth_hash
from django.db import transaction
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from .models import ConfiguracionSistema, Expediente, PerfilUsuario
from .rut import normalizar_rut
from .serializers import (
    CambiarClaveSerializer, ConfiguracionSistemaSerializer, CuentaSerializer,
    ImportarCuentasSerializer,
)


class EsAdministradorActivo(BasePermission):
    def has_permission(self, request, view):
        perfil = getattr(request.user, "perfil", None)
        return bool(request.user.is_authenticated and request.user.is_active and perfil
                    and perfil.activo and perfil.rol == PerfilUsuario.Rol.ADMINISTRADOR)


class CuentaViewSet(viewsets.ModelViewSet):
    queryset = PerfilUsuario.objects.select_related("usuario", "expediente").all().order_by("nombre", "id")
    serializer_class = CuentaSerializer
    permission_classes = [EsAdministradorActivo]
    filterset_fields = ["rol", "activo"]
    search_fields = ["nombre", "usuario__email"]

    def perform_update(self, serializer):
        perfil = self.get_object()
        if perfil.usuario_id == self.request.user.id:
            rol = serializer.validated_data.get("rol", perfil.rol)
            activo = serializer.validated_data.get("activo", perfil.activo)
            if rol != PerfilUsuario.Rol.ADMINISTRADOR or not activo:
                raise ValidationError("No puede quitarse su propio acceso de Administrador.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.usuario_id == self.request.user.id:
            raise ValidationError("No puede eliminar su propia cuenta.")
        instance.usuario.delete()

    @action(detail=False, methods=["post"], url_path="importar", serializer_class=ImportarCuentasSerializer,
            parser_classes=[MultiPartParser, FormParser])
    def importar(self, request):
        datos = self.get_serializer(data=request.data)
        datos.is_valid(raise_exception=True)
        archivo = datos.validated_data["archivo"]
        if archivo.size > 1024 * 1024:
            raise ValidationError("El CSV no puede superar 1 MB.")
        try:
            contenido = archivo.read().decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValidationError("El CSV debe estar codificado en UTF-8.") from error
        lector = csv.DictReader(io.StringIO(contenido))
        if lector.fieldnames != ["nombre", "email", "rut"]:
            raise ValidationError("Las columnas deben ser exactamente nombre,email,rut.")
        filas = list(lector)
        if len(filas) > 200:
            raise ValidationError("Se admiten hasta 200 estudiantes por archivo.")
        resultados = []
        correos_csv = set()
        for numero, fila in enumerate(filas, start=2):
            correo = (fila.get("email") or "").strip().lower()
            if correo in correos_csv:
                resultados.append({"fila": numero, "error": "Correo repetido en el CSV."})
                continue
            correos_csv.add(correo)
            try:
                rut = (fila.get("rut") or "").strip()
                expediente = None
                if rut:
                    expediente = Expediente.objects.filter(rut=normalizar_rut(rut)).first()
                    if expediente is None:
                        raise ValidationError("No existe un expediente con ese RUT.")
                clave = secrets.token_urlsafe(18) + "A1!"
                cuenta = CuentaSerializer(data={
                    "nombre": (fila.get("nombre") or "").strip(),
                    "email": correo, "password": clave,
                    "rol": PerfilUsuario.Rol.ESTUDIANTE,
                    "expediente": expediente.pk if expediente else None,
                }, context={"request": request})
                cuenta.is_valid(raise_exception=True)
                with transaction.atomic():
                    perfil = cuenta.save()
                resultados.append({"fila": numero, "email": correo, "id": perfil.pk, "clave_temporal": clave})
            except ValidationError as error:
                resultados.append({"fila": numero, "email": correo, "error": error.detail})
        return Response({"resultados": resultados}, status=status.HTTP_200_OK)


class CambiarClaveView(generics.GenericAPIView):
    serializer_class = CambiarClaveSerializer

    def post(self, request):
        datos = self.get_serializer(data=request.data)
        datos.is_valid(raise_exception=True)
        usuario = request.user
        if not usuario.check_password(datos.validated_data["clave_actual"]):
            raise ValidationError({"clave_actual": "La contraseña actual no coincide."})
        usuario.set_password(datos.validated_data["clave_nueva"])
        usuario.save(update_fields=["password"])
        update_session_auth_hash(request, usuario)
        return Response({"detalle": "Contraseña actualizada."})


class EstadoSistemaView(generics.GenericAPIView):
    serializer_class = ConfiguracionSistemaSerializer

    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [EsAdministradorActivo()]

    def get(self, request):
        estado, _ = ConfiguracionSistema.objects.get_or_create(pk=1)
        return Response(ConfiguracionSistemaSerializer(estado).data)

    def put(self, request):
        estado, _ = ConfiguracionSistema.objects.get_or_create(pk=1)
        datos = self.get_serializer(estado, data=request.data)
        datos.is_valid(raise_exception=True)
        datos.save()
        return Response(datos.data)

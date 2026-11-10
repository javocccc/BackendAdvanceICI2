from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import filters, generics, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import Acta, CambioColegiatura, Expediente, PerfilUsuario, RegistroAuditoria
from .permissions import PermisoActa, PermisoExpediente
from .serializers import (
    ActaSerializer, CambioColegiaturaSerializer, ExpedienteSerializer,
    NuevoEstadoColegiaturaSerializer, PerfilUsuarioSerializer, RegistroAuditoriaSerializer,
)


class ExpedienteViewSet(viewsets.ModelViewSet):
    serializer_class = ExpedienteSerializer
    permission_classes = [PermisoExpediente]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["colegiatura", "anio_egreso", "semestre_egreso"]
    search_fields = ["nombre", "rut", "profesor_guia"]
    ordering_fields = ["fecha_creacion", "fecha_examen", "nombre", "rut"]

    def get_queryset(self):
        perfil = getattr(self.request.user, "perfil", None)
        if not perfil or not perfil.activo:
            return Expediente.objects.none()
        if perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            return Expediente.objects.filter(pk=perfil.expediente_id)
        return Expediente.objects.all()

    def perform_create(self, serializer):
        expediente = serializer.save(creado_por=self.request.user)
        RegistroAuditoria.objects.create(
            expediente=expediente, usuario=self.request.user, accion="creacion", detalles="Expediente creado"
        )

    def perform_update(self, serializer):
        expediente = serializer.save()
        RegistroAuditoria.objects.create(
            expediente=expediente, usuario=self.request.user, accion="edicion", detalles="Expediente editado"
        )

    @action(detail=True, methods=["post"], url_path="colegiatura", serializer_class=NuevoEstadoColegiaturaSerializer)
    def cambiar_colegiatura(self, request, pk=None):
        if request.user.perfil.rol != PerfilUsuario.Rol.ADMINISTRADOR:
            raise PermissionDenied("Solo Administración puede cambiar la colegiatura.")
        datos = self.get_serializer(data=request.data)
        datos.is_valid(raise_exception=True)
        with transaction.atomic():
            expediente = Expediente.objects.select_for_update().get(pk=self.get_object().pk)
            anterior = expediente.colegiatura
            nuevo = datos.validated_data["estado_nuevo"]
            if anterior == nuevo:
                from rest_framework.exceptions import ValidationError
                raise ValidationError("El estado nuevo debe ser distinto del actual.")
            expediente.colegiatura = nuevo
            expediente.fecha_colegiatura = timezone.localdate()
            expediente.save(update_fields=["colegiatura", "fecha_colegiatura", "fecha_actualizacion"])
            cambio = CambioColegiatura.objects.create(
                expediente=expediente, estado_anterior=anterior, estado_nuevo=nuevo,
                usuario=request.user, observacion=datos.validated_data.get("observacion", ""),
            )
            RegistroAuditoria.objects.create(
                expediente=expediente, usuario=request.user, accion="colegiatura",
                detalles=f"{anterior} → {nuevo}",
            )
        return Response(CambioColegiaturaSerializer(cambio).data)

    @action(detail=True, methods=["get"], url_path="historial-colegiatura")
    def historial_colegiatura(self, request, pk=None):
        expediente = self.get_object()
        return Response(CambioColegiaturaSerializer(expediente.historial_colegiatura.all(), many=True).data)

    @action(detail=True, methods=["get"], url_path="auditoria")
    def auditoria(self, request, pk=None):
        expediente = self.get_object()
        if request.user.perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            raise PermissionDenied("El historial de auditoría es interno.")
        return Response(RegistroAuditoriaSerializer(expediente.auditoria.all(), many=True).data)


class ActaViewSet(viewsets.ModelViewSet):
    serializer_class = ActaSerializer
    permission_classes = [PermisoActa]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["expediente"]

    def get_queryset(self):
        perfil = getattr(self.request.user, "perfil", None)
        if not perfil or not perfil.activo:
            return Acta.objects.none()
        if perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            return Acta.objects.filter(expediente_id=perfil.expediente_id)
        return Acta.objects.all()

    def perform_create(self, serializer):
        acta = serializer.save(cargada_por=self.request.user)
        RegistroAuditoria.objects.create(
            expediente=acta.expediente, usuario=self.request.user, accion="acta", detalles="Acta cargada"
        )

    def perform_update(self, serializer):
        anterior = self.get_object().archivo
        acta = serializer.save()
        if anterior.name != acta.archivo.name:
            anterior.delete(save=False)
        RegistroAuditoria.objects.create(
            expediente=acta.expediente, usuario=self.request.user, accion="acta", detalles="Acta actualizada"
        )

    def perform_destroy(self, instance):
        archivo = instance.archivo
        expediente = instance.expediente
        instance.delete()
        archivo.delete(save=False)
        RegistroAuditoria.objects.create(
            expediente=expediente, usuario=self.request.user, accion="acta", detalles="Acta eliminada"
        )

    @action(detail=True, methods=["get"], url_path="descargar")
    def descargar(self, request, pk=None):
        acta = self.get_object()
        return FileResponse(acta.archivo.open("rb"), as_attachment=True, filename=acta.nombre_original)


class ResumenView(generics.GenericAPIView):
    permission_classes = [PermisoExpediente]

    def get(self, request):
        if request.user.perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            raise PermissionDenied("El resumen es para el personal del sistema.")
        datos = Expediente.objects.aggregate(
            total=Count("id"),
            pendientes_colegiatura=Count("id", filter=Q(colegiatura__in=["no-al-dia", "sin-informar"])),
            pendientes_acta=Count("id", filter=Q(acta__isnull=True)),
        )
        return Response(datos)


class PerfilActualView(generics.RetrieveAPIView):
    serializer_class = PerfilUsuarioSerializer

    def get_object(self):
        perfil = self.request.user.perfil
        if not perfil.activo:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("La cuenta está desactivada.")
        return perfil

from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import filters, generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .documentos import MAX_ARCHIVOS, validar_documento
from .models import Acta, CambioColegiatura, DocumentoExpediente, Expediente, PerfilUsuario, RegistroAuditoria
from .permissions import PermisoActa, PermisoExpediente
from .serializers import (
    ActaSerializer, CambioColegiaturaSerializer, CargaDocumentosExpedienteSerializer,
    CargaDocumentosSerializer,
    DocumentoExpedienteSerializer, ExpedienteSerializer,
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

    @action(
        detail=True, methods=["get", "post"], url_path="documentos",
        parser_classes=[MultiPartParser, FormParser], serializer_class=CargaDocumentosExpedienteSerializer,
    )
    def documentos(self, request, pk=None):
        expediente = self.get_object()
        if request.method == "GET":
            return Response(DocumentoExpedienteSerializer(
                expediente.documentos.all(), many=True, context={"request": request}
            ).data)
        return cargar_documentos(request, [expediente] * len(request.FILES.getlist("archivos")),
                               claves_permitidas={"archivos"})


def cargar_documentos(request, expedientes, *, claves_permitidas):
    archivos = request.FILES.getlist("archivos")
    extras = set(request.data) - claves_permitidas
    if extras:
        raise ValidationError({campo: "Campo no admitido." for campo in sorted(extras)})
    if not 1 <= len(archivos) <= MAX_ARCHIVOS:
        raise ValidationError({"archivos": f"Envíe entre 1 y {MAX_ARCHIVOS} archivos."})
    if len(expedientes) != len(archivos):
        raise ValidationError({"expedientes": "Debe indicar un expediente por cada archivo."})

    # Se valida el lote completo antes de guardar; un archivo incorrecto impide toda la carga.
    datos = [validar_documento(archivo) for archivo in archivos]
    creados = []
    archivos_guardados = []
    try:
        with transaction.atomic():
            for (archivo, expediente, (nombre, tipo)) in zip(archivos, expedientes, datos):
                documento = DocumentoExpediente(
                    expediente=expediente, nombre_original=nombre,
                    tipo=tipo, tamano=archivo.size, cargado_por=request.user,
                )
                documento.archivo.save(nombre, archivo, save=False)
                archivos_guardados.append((documento.archivo.storage, documento.archivo.name))
                documento.save()
                creados.append(documento)
                RegistroAuditoria.objects.create(
                    expediente=expediente, usuario=request.user, accion="documento",
                    detalles=f"Archivo cargado: {nombre}",
                )
    except Exception:
        # El rollback de la BD no borra archivos ya escritos en el almacenamiento.
        for almacenamiento, nombre in archivos_guardados:
            almacenamiento.delete(nombre)
        raise
    return Response(DocumentoExpedienteSerializer(
        creados, many=True, context={"request": request}
    ).data, status=status.HTTP_201_CREATED)


class DocumentoExpedienteViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    serializer_class = DocumentoExpedienteSerializer
    permission_classes = [PermisoActa]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["expediente", "tipo"]

    def get_queryset(self):
        perfil = getattr(self.request.user, "perfil", None)
        if not perfil or not perfil.activo:
            return DocumentoExpediente.objects.none()
        if perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            return DocumentoExpediente.objects.filter(expediente_id=perfil.expediente_id)
        return DocumentoExpediente.objects.all()

    @action(detail=False, methods=["get", "post"], url_path="carga-masiva", serializer_class=CargaDocumentosSerializer)
    def carga_masiva(self, request):
        if request.method == "GET":
            return Response({"archivos": [], "expedientes": ""})
        archivos = request.FILES.getlist("archivos")
        ids = request.data.getlist("expedientes")
        # El formulario navegable envía "1,2"; otros clientes pueden repetir la clave.
        if len(ids) == 1 and "," in ids[0]:
            ids = [valor.strip() for valor in ids[0].split(",")]
        if len(ids) != len(archivos):
            raise ValidationError({"expedientes": "Repita el identificador del expediente por cada archivo."})
        if any(not valor.isdecimal() or int(valor) < 1 for valor in ids):
            raise ValidationError({"expedientes": "Cada identificador debe ser un entero positivo."})
        encontrados = Expediente.objects.in_bulk(int(valor) for valor in ids)
        if len(encontrados) != len(set(map(int, ids))):
            raise ValidationError({"expedientes": "Uno o más expedientes no existen."})
        destinos = [encontrados[int(valor)] for valor in ids]
        return cargar_documentos(request, destinos, claves_permitidas={"archivos", "expedientes"})

    def perform_destroy(self, instance):
        archivo = instance.archivo
        expediente = instance.expediente
        nombre = instance.nombre_original
        instance.delete()
        archivo.delete(save=False)
        RegistroAuditoria.objects.create(
            expediente=expediente, usuario=self.request.user, accion="documento",
            detalles=f"Archivo eliminado: {nombre}",
        )

    @action(detail=True, methods=["get"], url_path="descargar")
    def descargar(self, request, pk=None):
        documento = self.get_object()
        return FileResponse(documento.archivo.open("rb"), as_attachment=True,
                            filename=documento.nombre_original)


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

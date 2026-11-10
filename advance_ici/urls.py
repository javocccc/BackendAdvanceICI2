from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from gestion.views import ActaViewSet, ExpedienteViewSet, PerfilActualView, ResumenView
from gestion.cuentas import CambiarClaveView, CuentaViewSet, EstadoSistemaView

router = DefaultRouter()
router.register("expedientes", ExpedienteViewSet, basename="expediente")
router.register("actas", ActaViewSet, basename="acta")
router.register("cuentas", CuentaViewSet, basename="cuenta")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/mi-perfil/", PerfilActualView.as_view(), name="mi-perfil"),
    path("api/resumen/", ResumenView.as_view(), name="resumen"),
    path("api/cambiar-clave/", CambiarClaveView.as_view(), name="cambiar-clave"),
    path("api/estado/", EstadoSistemaView.as_view(), name="estado-sistema"),
    path("api-auth/", include("rest_framework.urls")),
]

from django.contrib import admin

from .models import Acta, CambioColegiatura, ConfiguracionSistema, DocumentoExpediente, Expediente, PerfilUsuario, RegistroAuditoria


@admin.register(Expediente)
class ExpedienteAdmin(admin.ModelAdmin):
    list_display = ("rut", "nombre", "fecha_examen", "colegiatura")
    search_fields = ("rut", "nombre")
    list_filter = ("colegiatura", "anio_egreso")


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "usuario", "rol", "activo", "expediente")
    list_filter = ("rol", "activo")


admin.site.register(Acta)
admin.site.register(DocumentoExpediente)
admin.site.register(CambioColegiatura)
admin.site.register(RegistroAuditoria)
admin.site.register(ConfiguracionSistema)

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from uuid import uuid4


class PerfilUsuario(models.Model):
    class Rol(models.TextChoices):
        ADMINISTRADOR = "administrador", "Administrador"
        COORDINADOR = "coordinador", "Coordinador"
        ESTUDIANTE = "estudiante", "Estudiante"

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    nombre = models.CharField(max_length=180)
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.ESTUDIANTE)
    activo = models.BooleanField(default=True)
    expediente = models.OneToOneField(
        "Expediente", on_delete=models.SET_NULL, null=True, blank=True, related_name="perfil_estudiante"
    )

    def __str__(self):
        return f"{self.nombre} ({self.get_rol_display()})"


class Expediente(models.Model):
    class EstadoColegiatura(models.TextChoices):
        AL_DIA = "al-dia", "Al día"
        NO_AL_DIA = "no-al-dia", "No al día"
        SIN_INFORMAR = "sin-informar", "Sin informar"

    nombre = models.CharField(max_length=180)
    rut = models.CharField(max_length=12, unique=True, validators=[
        RegexValidator(r"^\d{1,2}\.\d{3}\.\d{3}-[\dK]$", "Use el formato 12.345.678-5.")
    ])
    anio_egreso = models.PositiveSmallIntegerField(validators=[MinValueValidator(2020), MaxValueValidator(2050)])
    semestre_egreso = models.PositiveSmallIntegerField(choices=[(1, "Primer semestre"), (2, "Segundo semestre")])
    fecha_examen = models.DateField()
    nota_examen = models.DecimalField(
        max_digits=2, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    profesor_guia = models.CharField(max_length=180, blank=True)
    informante_1 = models.CharField(max_length=180, blank=True)
    informante_2 = models.CharField(max_length=180, blank=True)
    informante_adicional = models.CharField(max_length=180, blank=True)
    colegiatura = models.CharField(
        max_length=12, choices=EstadoColegiatura.choices, default=EstadoColegiatura.SIN_INFORMAR
    )
    fecha_colegiatura = models.DateField(null=True, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="expedientes_creados"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]

    def __str__(self):
        return f"{self.rut} · {self.nombre}"


def ruta_acta(instance, filename):
    # Un nombre aleatorio evita colisiones y no expone el nombre entregado por el usuario.
    return f"actas/{instance.expediente_id}/{uuid4().hex}.pdf"


class Acta(models.Model):
    expediente = models.OneToOneField(Expediente, on_delete=models.CASCADE, related_name="acta")
    archivo = models.FileField(upload_to=ruta_acta)
    nombre_original = models.CharField(max_length=255)
    fecha_carga = models.DateTimeField(auto_now_add=True)
    cargada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"Acta de {self.expediente.rut}"


class CambioColegiatura(models.Model):
    expediente = models.ForeignKey(Expediente, on_delete=models.CASCADE, related_name="historial_colegiatura")
    estado_anterior = models.CharField(max_length=12, choices=Expediente.EstadoColegiatura.choices)
    estado_nuevo = models.CharField(max_length=12, choices=Expediente.EstadoColegiatura.choices)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    observacion = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha", "-id"]


class RegistroAuditoria(models.Model):
    expediente = models.ForeignKey(Expediente, on_delete=models.CASCADE, related_name="auditoria")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    accion = models.CharField(max_length=80)
    detalles = models.TextField(blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]


class ConfiguracionSistema(models.Model):
    mantenimiento = models.BooleanField(default=False)
    mensaje = models.CharField(max_length=300, blank=True)
    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Estado de Advance ICI"

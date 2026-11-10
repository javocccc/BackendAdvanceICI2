from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import PerfilUsuario


@receiver(post_save, sender=get_user_model())
def crear_perfil_usuario(sender, instance, created, **kwargs):
    # El primer superusuario obtiene permisos de Administración al crearse.
    if created:
        PerfilUsuario.objects.create(
            usuario=instance,
            nombre=instance.get_full_name() or instance.username,
            rol=PerfilUsuario.Rol.ADMINISTRADOR if instance.is_superuser else PerfilUsuario.Rol.ESTUDIANTE,
        )

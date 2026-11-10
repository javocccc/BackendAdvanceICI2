from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from gestion.models import PerfilUsuario


class Command(BaseCommand):
    help = "Prepara el usuario Administrador para una demostración local."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        usuario, _ = get_user_model().objects.get_or_create(
            username="root", defaults={"email": "root@advanceici.local"}
        )
        usuario.set_password(options["password"])
        usuario.is_active = True
        usuario.is_staff = True
        usuario.is_superuser = True
        usuario.save()
        perfil = usuario.perfil
        perfil.nombre = "root"
        perfil.rol = PerfilUsuario.Rol.ADMINISTRADOR
        perfil.activo = True
        perfil.save()
        self.stdout.write(self.style.SUCCESS("Usuario root listo para ingresar."))

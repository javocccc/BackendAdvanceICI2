from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from gestion.models import Expediente, PerfilUsuario


class Command(BaseCommand):
    help = "Crea datos ficticios para ensayar la API navegable en un entorno local."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Contraseña local para las tres cuentas de demostración")

    def handle(self, *args, **options):
        password = options["password"]
        if not password:
            raise ValueError("Indique una contraseña para las cuentas locales.")
        usuarios = get_user_model()
        cuentas = (
            ("root", "root@advanceici.local", "Administrador", PerfilUsuario.Rol.ADMINISTRADOR),
            ("coord@advanceici.local", "coord@advanceici.local", "Coordinador", PerfilUsuario.Rol.COORDINADOR),
            ("alumno@advanceici.local", "alumno@advanceici.local", "Estudiante", PerfilUsuario.Rol.ESTUDIANTE),
        )
        creados = {}
        for username, email, nombre, rol in cuentas:
            usuario, _ = usuarios.objects.get_or_create(username=username, defaults={"email": email})
            usuario.set_password(password)
            usuario.is_staff = rol == PerfilUsuario.Rol.ADMINISTRADOR
            usuario.is_superuser = rol == PerfilUsuario.Rol.ADMINISTRADOR
            usuario.save()
            perfil = usuario.perfil
            perfil.nombre, perfil.rol, perfil.activo = nombre, rol, True
            perfil.save()
            creados[rol] = usuario
        muestras = (
            ("12.345.678-5", "Camila Rojas", date(2026, 10, 15)),
            ("11.111.111-1", "Diego Pérez", date(2026, 10, 22)),
            ("24.680.246-7", "Valentina Soto Araya", date(2026, 11, 20)),
        )
        for rut, nombre, fecha in muestras:
            Expediente.objects.get_or_create(rut=rut, defaults={
                "nombre": nombre, "anio_egreso": 2025, "semestre_egreso": 2,
                "fecha_examen": fecha, "creado_por": creados[PerfilUsuario.Rol.ADMINISTRADOR],
            })
        estudiante = creados[PerfilUsuario.Rol.ESTUDIANTE].perfil
        estudiante.expediente = Expediente.objects.get(rut="12.345.678-5")
        estudiante.save(update_fields=["expediente"])
        self.stdout.write(self.style.SUCCESS("Datos ficticios listos para la API navegable."))

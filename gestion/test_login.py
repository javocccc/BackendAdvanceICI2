from django.core.management import call_command
from django.test import TestCase

from .models import PerfilUsuario


class InicioSesionTests(TestCase):
    def test_login_abre_la_api_navegable(self):
        call_command("crear_root_local", password="123456", verbosity=0)
        respuesta = self.client.post("/api-auth/login/", {
            "username": "root", "password": "123456",
        })
        self.assertRedirects(respuesta, "/api/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/api/").status_code, 200)
        self.assertEqual(PerfilUsuario.objects.get(usuario__username="root").rol,
                         PerfilUsuario.Rol.ADMINISTRADOR)

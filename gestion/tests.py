from datetime import date
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Acta, CambioColegiatura, Expediente, PerfilUsuario, RegistroAuditoria


class ExpedienteAPITests(TestCase):
    def setUp(self):
        usuarios = get_user_model()
        self.admin = usuarios.objects.create_superuser("admin", "admin@example.com", "ClaveSegura123!")
        self.coordinador = usuarios.objects.create_user("coord", "coord@example.com", "ClaveSegura123!")
        self.coordinador.perfil.rol = PerfilUsuario.Rol.COORDINADOR
        self.coordinador.perfil.save()
        self.estudiante = usuarios.objects.create_user("alumno", "alumno@example.com", "ClaveSegura123!")
        self.expediente = Expediente.objects.create(
            nombre="Camila Rojas", rut="12.345.678-5", anio_egreso=2025,
            semestre_egreso=2, fecha_examen=date(2026, 10, 15), creado_por=self.admin,
        )
        self.otro = Expediente.objects.create(
            nombre="Diego Pérez", rut="11.111.111-1", anio_egreso=2024,
            semestre_egreso=1, fecha_examen=date(2026, 9, 10), creado_por=self.admin,
        )
        self.estudiante.perfil.expediente = self.expediente
        self.estudiante.perfil.save()
        self.client = APIClient()

    def test_anonimo_no_puede_leer(self):
        self.assertEqual(self.client.get("/api/expedientes/").status_code, 403)

    def test_estudiante_solo_lee_su_expediente(self):
        self.client.force_authenticate(self.estudiante)
        respuesta = self.client.get("/api/expedientes/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["count"], 1)
        self.assertEqual(self.client.get(f"/api/expedientes/{self.otro.pk}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/expedientes/{self.expediente.pk}/").status_code, 403)

    def test_coordinador_hace_crud_y_busca(self):
        self.client.force_authenticate(self.coordinador)
        nuevo = {
            "nombre": "Valentina Soto", "rut": "22.222.222-2", "anio_egreso": 2026,
            "semestre_egreso": 1, "fecha_examen": "2026-11-04", "nota_examen": "6.2",
            "profesor_guia": "María Fuentes", "informante_1": "Carlos Vega",
            "informante_2": "Paula Torres",
        }
        creado = self.client.post("/api/expedientes/", nuevo)
        self.assertEqual(creado.status_code, 201, creado.data)
        pk = creado.data["id"]
        self.assertEqual(creado.data["creado_por"], "coord")
        self.assertEqual(self.client.get("/api/expedientes/?search=Valentina").data["count"], 1)
        self.assertEqual(self.client.get("/api/expedientes/?anio_egreso=2026").data["count"], 1)
        self.assertEqual(self.client.patch(f"/api/expedientes/{pk}/", {"nombre": "Valentina Soto A."}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/expedientes/{pk}/").status_code, 204)

    def test_rut_invalido_y_duplicado(self):
        self.client.force_authenticate(self.admin)
        datos = {
            "nombre": "Otro", "rut": "12.345.678-0", "anio_egreso": 2025,
            "semestre_egreso": 1, "fecha_examen": "2026-11-04", "nota_examen": "6.1",
            "profesor_guia": "María Fuentes", "informante_1": "Carlos Vega",
            "informante_2": "Paula Torres",
        }
        self.assertEqual(self.client.post("/api/expedientes/", datos).status_code, 400)
        datos["rut"] = "123456785"
        self.assertEqual(self.client.post("/api/expedientes/", datos).status_code, 400)

    def test_paginacion(self):
        self.client.force_authenticate(self.admin)
        respuesta = self.client.get("/api/expedientes/?page=1")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("results", respuesta.data)

    def test_solo_administracion_cambia_colegiatura(self):
        url = f"/api/expedientes/{self.expediente.pk}/colegiatura/"
        self.client.force_authenticate(self.coordinador)
        self.assertEqual(self.client.post(url, {"estado_nuevo": "al-dia"}).status_code, 403)
        self.client.force_authenticate(self.admin)
        respuesta = self.client.post(url, {"estado_nuevo": "al-dia", "observacion": "Pago registrado"})
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(CambioColegiatura.objects.filter(expediente=self.expediente).count(), 1)
        self.assertEqual(self.client.get(f"/api/expedientes/{self.expediente.pk}/historial-colegiatura/").data[0]["estado_nuevo"], "al-dia")
        self.assertTrue(RegistroAuditoria.objects.filter(expediente=self.expediente, accion="colegiatura").exists())

    def test_acta_pdf_y_descarga(self):
        carpeta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, carpeta, True)
        with override_settings(MEDIA_ROOT=carpeta):
            self.client.force_authenticate(self.coordinador)
            archivo = SimpleUploadedFile("acta.pdf", b"%PDF-1.4\ncontenido", content_type="application/pdf")
            respuesta = self.client.post("/api/actas/", {"expediente": self.expediente.pk, "archivo": archivo}, format="multipart")
            self.assertEqual(respuesta.status_code, 201, respuesta.data)
            self.assertEqual(Acta.objects.count(), 1)
            self.client.force_authenticate(self.estudiante)
            self.assertEqual(self.client.get(f"/api/actas/{respuesta.data['id']}/descargar/").status_code, 200)
            self.assertEqual(self.client.delete(f"/api/actas/{respuesta.data['id']}/").status_code, 403)

    def test_rechaza_acta_contenido_invalido(self):
        self.client.force_authenticate(self.admin)
        archivo = SimpleUploadedFile("acta.pdf", b"no-es-pdf", content_type="application/pdf")
        respuesta = self.client.post("/api/actas/", {"expediente": self.expediente.pk, "archivo": archivo}, format="multipart")
        self.assertEqual(respuesta.status_code, 400)

    def test_cuentas_reservadas_a_administracion(self):
        self.client.force_authenticate(self.coordinador)
        self.assertEqual(self.client.get("/api/cuentas/").status_code, 403)
        self.client.force_authenticate(self.admin)
        datos = {"email": "nuevo@example.com", "password": "ClaveNueva123!", "nombre": "Alumno Nuevo",
                 "rol": "estudiante", "activo": True, "expediente": self.otro.pk}
        creado = self.client.post("/api/cuentas/", datos)
        self.assertEqual(creado.status_code, 201, creado.data)
        self.assertNotIn("password", creado.data)
        self.assertEqual(PerfilUsuario.objects.get(pk=creado.data["id"]).expediente_id, self.otro.pk)
        self.assertEqual(self.client.delete(f"/api/cuentas/{creado.data['id']}/").status_code, 204)

    def test_importacion_csv_y_clave(self):
        self.client.force_authenticate(self.admin)
        archivo = SimpleUploadedFile("alumnos.csv", b"nombre,email,rut\nPrueba,prueba@example.com,\n", content_type="text/csv")
        respuesta = self.client.post("/api/cuentas/importar/", {"archivo": archivo}, format="multipart")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertIn("clave_temporal", respuesta.data["resultados"][0])
        self.assertTrue(get_user_model().objects.filter(email="prueba@example.com").exists())

    def test_estado_publico_y_edicion_admin(self):
        self.assertEqual(self.client.get("/api/estado/").status_code, 200)
        self.client.force_authenticate(self.coordinador)
        self.assertEqual(self.client.put("/api/estado/", {"mantenimiento": True}).status_code, 403)
        self.client.force_authenticate(self.admin)
        respuesta = self.client.put("/api/estado/", {"mantenimiento": True, "mensaje": "Pausa"})
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(respuesta.data["mantenimiento"])

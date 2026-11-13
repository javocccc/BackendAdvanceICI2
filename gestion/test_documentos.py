from datetime import date
from io import BytesIO
import shutil
import tempfile
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import DocumentoExpediente, Expediente, PerfilUsuario, RegistroAuditoria


def archivo(nombre, contenido=b"%PDF-1.4\ncontenido"):
    return SimpleUploadedFile(nombre, contenido)


def office(nombre, parte):
    salida = BytesIO()
    with ZipFile(salida, "w") as paquete:
        paquete.writestr("[Content_Types].xml", "<Types/>")
        paquete.writestr(parte, "<document/>")
    return archivo(nombre, salida.getvalue())


class DocumentosAPITests(TestCase):
    def setUp(self):
        usuarios = get_user_model()
        self.admin = usuarios.objects.create_superuser("admin-doc", "admin-doc@example.com", "ClaveSegura123!")
        self.coordinador = usuarios.objects.create_user("coord-doc", "coord-doc@example.com", "ClaveSegura123!")
        self.coordinador.perfil.rol = PerfilUsuario.Rol.COORDINADOR
        self.coordinador.perfil.save()
        self.estudiante = usuarios.objects.create_user("alumno-doc", "alumno-doc@example.com", "ClaveSegura123!")
        self.uno = Expediente.objects.create(
            nombre="Uno", rut="12.345.678-5", anio_egreso=2025, semestre_egreso=1,
            fecha_examen=date(2026, 11, 10), creado_por=self.admin,
        )
        self.dos = Expediente.objects.create(
            nombre="Dos", rut="11.111.111-1", anio_egreso=2025, semestre_egreso=2,
            fecha_examen=date(2026, 11, 11), creado_por=self.admin,
        )
        self.estudiante.perfil.expediente = self.uno
        self.estudiante.perfil.save()
        self.client = APIClient()
        self.client.force_authenticate(self.coordinador)
        carpeta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, carpeta, True)
        media = override_settings(MEDIA_ROOT=carpeta)
        media.enable()
        self.addCleanup(media.disable)

    def test_varios_archivos_para_un_expediente_y_descarga(self):
        url = f"/api/expedientes/{self.uno.pk}/documentos/"
        respuesta = self.client.post(url, {
            "archivos": [archivo("informe.pdf"), archivo("observaciones.txt", b"Texto de prueba")],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(len(respuesta.data), 2)
        self.assertEqual(DocumentoExpediente.objects.filter(expediente=self.uno).count(), 2)
        self.assertEqual(RegistroAuditoria.objects.filter(expediente=self.uno, accion="documento").count(), 2)
        self.assertEqual(len(self.client.get(url).data), 2)
        documento = respuesta.data[0]
        descarga = self.client.get(f"/api/documentos/{documento['id']}/descargar/")
        self.assertEqual(descarga.status_code, 200)
        self.assertIn("informe.pdf", descarga["Content-Disposition"])

    def test_carga_masiva_entre_expedientes_y_formatos(self):
        respuesta = self.client.post("/api/documentos/carga-masiva/", {
            "expedientes": [str(self.uno.pk), str(self.dos.pk), str(self.uno.pk), str(self.dos.pk)],
            "archivos": [
                office("informe.docx", "word/document.xml"),
                office("notas.xlsx", "xl/workbook.xml"),
                archivo("imagen.png", b"\x89PNG\r\n\x1a\ncontenido"),
                archivo("foto.jpg", b"\xff\xd8\xffcontenido"),
            ],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual([item["expediente"] for item in respuesta.data],
                         [self.uno.pk, self.dos.pk, self.uno.pk, self.dos.pk])
        self.assertEqual(DocumentoExpediente.objects.count(), 4)
        self.assertEqual(DocumentoExpediente.objects.filter(tipo="docx").count(), 1)

    def test_formulario_acepta_ids_separados_por_comas(self):
        respuesta = self.client.post("/api/documentos/carga-masiva/", {
            "expedientes": f"{self.uno.pk}, {self.dos.pk}",
            "archivos": [archivo("a.pdf"), archivo("b.pdf")],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual([item["expediente"] for item in respuesta.data], [self.uno.pk, self.dos.pk])

    def test_formulario_navegable_acepta_token_csrf_en_ambas_modalidades(self):
        una_ficha = self.client.post(f"/api/expedientes/{self.uno.pk}/documentos/", {
            "csrfmiddlewaretoken": "token-del-formulario",
            "archivos": [archivo("primero.pdf"), archivo("segundo.pdf")],
        }, format="multipart")
        self.assertEqual(una_ficha.status_code, 201, una_ficha.data)
        self.assertEqual(len(una_ficha.data), 2)

        varias_fichas = self.client.post("/api/documentos/carga-masiva/", {
            "csrfmiddlewaretoken": "token-del-formulario",
            "expedientes": f"{self.uno.pk},{self.dos.pk}",
            "archivos": [archivo("tercero.pdf"), archivo("cuarto.pdf")],
        }, format="multipart")
        self.assertEqual(varias_fichas.status_code, 201, varias_fichas.data)
        self.assertEqual([item["expediente"] for item in varias_fichas.data], [self.uno.pk, self.dos.pk])
        self.assertEqual(DocumentoExpediente.objects.count(), 4)

    def test_api_navegable_muestra_selector_multiple(self):
        respuesta = self.client.get("/api/documentos/carga-masiva/", HTTP_ACCEPT="text/html")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(b'type="file" multiple', respuesta.content)

    def test_lote_invalido_no_guarda_documentos(self):
        url = f"/api/expedientes/{self.uno.pk}/documentos/"
        respuesta = self.client.post(url, {
            "archivos": [archivo("bueno.pdf"), archivo("falso.pdf", b"no es pdf")],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertEqual(DocumentoExpediente.objects.count(), 0)
        for nombre, contenido in (("programa.exe", b"MZ"), ("falso.docx", b"no es office")):
            with self.subTest(nombre=nombre):
                respuesta = self.client.post(url, {"archivos": [archivo(nombre, contenido)]}, format="multipart")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertEqual(DocumentoExpediente.objects.count(), 0)

    def test_destinos_invalidos_y_cantidad_desigual(self):
        for ids in ([str(self.uno.pk), "9999"], [str(self.uno.pk)], ["-1", str(self.dos.pk)]):
            with self.subTest(ids=ids):
                respuesta = self.client.post("/api/documentos/carga-masiva/", {
                    "expedientes": ids,
                    "archivos": [archivo("a.pdf"), archivo("b.pdf")],
                }, format="multipart")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertEqual(DocumentoExpediente.objects.count(), 0)

    def test_estudiante_solo_consulta_sus_documentos(self):
        respuesta = self.client.post("/api/documentos/carga-masiva/", {
            "expedientes": [str(self.uno.pk), str(self.dos.pk)],
            "archivos": [archivo("a.pdf"), archivo("b.pdf")],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        propio, ajeno = respuesta.data
        self.client.force_authenticate(self.estudiante)
        self.assertEqual(self.client.get("/api/documentos/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/documentos/{propio['id']}/descargar/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/documentos/{ajeno['id']}/descargar/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/expedientes/{self.uno.pk}/documentos/", {
            "archivos": [archivo("nuevo.pdf")],
        }, format="multipart").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/documentos/{propio['id']}/").status_code, 403)

    def test_eliminar_documento_borra_archivo(self):
        respuesta = self.client.post(f"/api/expedientes/{self.uno.pk}/documentos/", {
            "archivos": [archivo("a.pdf")],
        }, format="multipart")
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        documento = DocumentoExpediente.objects.get(pk=respuesta.data[0]["id"])
        self.assertTrue(documento.archivo.storage.exists(documento.archivo.name))
        self.assertEqual(self.client.delete(f"/api/documentos/{documento.pk}/").status_code, 204)
        self.assertFalse(documento.archivo.storage.exists(documento.archivo.name))

from datetime import date
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Expediente


class ValidadoresAPITests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "admin-validacion", "admin-validacion@example.com", "ClaveSegura123!"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.expediente_valido = {
            "nombre": "Valentina Soto", "rut": "22.222.222-2",
            "anio_egreso": 2026, "semestre_egreso": 1,
            "fecha_examen": "2026-11-10", "nota_examen": "6.3",
            "profesor_guia": "María Fuentes", "informante_1": "Carlos Vega",
            "informante_2": "Paula Torres",
        }

    def test_rechaza_numeros_fuera_de_rango_y_fechas_invalidas(self):
        casos = [
            ("anio_egreso", -1), ("anio_egreso", 2019), ("anio_egreso", 2051),
            ("semestre_egreso", 0), ("semestre_egreso", 3),
            ("nota_examen", "-1.0"), ("nota_examen", "0.0"),
            ("nota_examen", "7.1"), ("nota_examen", "6.33"),
            ("fecha_examen", "2026-02-30"), ("fecha_examen", "10/11/2026"),
        ]
        for campo, valor in casos:
            with self.subTest(campo=campo, valor=valor):
                datos = {**self.expediente_valido, campo: valor}
                respuesta = self.client.post("/api/expedientes/", datos, format="json")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
                self.assertIn(campo, respuesta.data)
        self.assertEqual(Expediente.objects.count(), 0)

    def test_rechaza_campos_obligatorios_y_docentes_repetidos(self):
        for campo in ("nota_examen", "profesor_guia", "informante_1", "informante_2"):
            with self.subTest(campo=campo):
                datos = self.expediente_valido.copy()
                datos.pop(campo)
                respuesta = self.client.post("/api/expedientes/", datos, format="json")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
                self.assertIn(campo, respuesta.data)
        repetidos = {**self.expediente_valido, "informante_1": "Maria   Fuentes"}
        respuesta = self.client.post("/api/expedientes/", repetidos, format="json")
        self.assertEqual(respuesta.status_code, 400, respuesta.data)

    def test_nombre_expediente_no_admite_cifras(self):
        for nombre in ("2", "Camila2", "123456"):
            with self.subTest(nombre=nombre):
                respuesta = self.client.post(
                    "/api/expedientes/", {**self.expediente_valido, "nombre": nombre}, format="json"
                )
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
                self.assertIn("nombre", respuesta.data)
        valido = self.client.post(
            "/api/expedientes/", {**self.expediente_valido, "nombre": "María-José O'Connor"}, format="json"
        )
        self.assertEqual(valido.status_code, 201, valido.data)
        edicion = self.client.patch(
            f"/api/expedientes/{valido.data['id']}/", {"nombre": "2"}, format="json"
        )
        self.assertEqual(edicion.status_code, 400, edicion.data)
        self.assertEqual(Expediente.objects.get(pk=valido.data["id"]).nombre, "María-José O'Connor")

    def test_docentes_rechazan_cifras_al_crear_y_editar(self):
        campos = ("profesor_guia", "informante_1", "informante_2", "informante_adicional")
        for campo in campos:
            for nombre in ("2", "Ana2"):
                with self.subTest(campo=campo, nombre=nombre):
                    respuesta = self.client.post(
                        "/api/expedientes/", {**self.expediente_valido, campo: nombre}, format="json"
                    )
                    self.assertEqual(respuesta.status_code, 400, respuesta.data)
                    self.assertIn(campo, respuesta.data)
        formulario = self.client.post(
            "/api/expedientes/", {**self.expediente_valido, "profesor_guia": "2"}, format="multipart"
        )
        self.assertEqual(formulario.status_code, 400, formulario.data)
        self.assertIn("profesor_guia", formulario.data)

        creado = self.client.post(
            "/api/expedientes/",
            {**self.expediente_valido, "informante_adicional": "Ana-María O'Connor"},
            format="json",
        )
        self.assertEqual(creado.status_code, 201, creado.data)
        expediente = Expediente.objects.get(pk=creado.data["id"])
        for campo in campos:
            with self.subTest(edicion=campo):
                anterior = getattr(expediente, campo)
                respuesta = self.client.patch(
                    f"/api/expedientes/{expediente.pk}/", {campo: "2"}, format="json"
                )
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
                self.assertIn(campo, respuesta.data)
                expediente.refresh_from_db()
                self.assertEqual(getattr(expediente, campo), anterior)
        quitar_opcional = self.client.patch(
            f"/api/expedientes/{expediente.pk}/", {"informante_adicional": ""}, format="json"
        )
        self.assertEqual(quitar_opcional.status_code, 200, quitar_opcional.data)

    def test_raw_json_rechaza_claves_ajenas_y_campos_de_solo_lectura(self):
        for campo, valor in (("anioEgreso", 2026), ("colegiatura", "al-dia"), ("id", 99)):
            with self.subTest(campo=campo):
                datos = {**self.expediente_valido, campo: valor}
                respuesta = self.client.post("/api/expedientes/", datos, format="json")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
                self.assertIn(campo, respuesta.data)

    def test_acepta_valores_validos_y_rechaza_rut_duplicado(self):
        datos = {**self.expediente_valido, "rut": "222222222"}
        creado = self.client.post("/api/expedientes/", datos, format="json")
        self.assertEqual(creado.status_code, 201, creado.data)
        self.assertEqual(creado.data["rut"], "22.222.222-2")
        duplicado = self.client.post("/api/expedientes/", self.expediente_valido, format="json")
        self.assertEqual(duplicado.status_code, 400, duplicado.data)
        self.assertIn("rut", duplicado.data)
        basura = self.client.post(
            "/api/expedientes/", {**self.expediente_valido, "rut": "abc22.222.222-2"}, format="json"
        )
        self.assertEqual(basura.status_code, 400, basura.data)
        formato = self.client.post(
            "/api/expedientes/", {**self.expediente_valido, "rut": "22-222-222-2"}, format="json"
        )
        self.assertEqual(formato.status_code, 400, formato.data)

    def test_cuenta_rechaza_email_rol_y_clave_invalidos(self):
        base = {"email": "nuevo@example.com", "password": "ClaveSegura123!",
                "nombre": "Alumno Nuevo", "rol": "estudiante", "activo": True}
        casos = [("email", "sin-correo"), ("rol", "profesor"), ("password", "123456")]
        for campo, valor in casos:
            with self.subTest(campo=campo):
                respuesta = self.client.post("/api/cuentas/", {**base, campo: valor}, format="json")
                self.assertEqual(respuesta.status_code, 400, respuesta.data)
        respuesta = self.client.post("/api/cuentas/", {**base, "rolNuevo": "administrador"}, format="json")
        self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertIn("rolNuevo", respuesta.data)

    def test_acta_no_se_mueve_a_expediente_con_acta_existente(self):
        primero = Expediente.objects.create(
            nombre="Primero", rut="12.345.678-5", anio_egreso=2025,
            semestre_egreso=1, fecha_examen=date(2026, 11, 10), creado_por=self.admin,
        )
        segundo = Expediente.objects.create(
            nombre="Segundo", rut="11.111.111-1", anio_egreso=2025,
            semestre_egreso=1, fecha_examen=date(2026, 11, 10), creado_por=self.admin,
        )
        carpeta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, carpeta, True)
        with override_settings(MEDIA_ROOT=carpeta):
            ids = []
            for expediente in (primero, segundo):
                archivo = SimpleUploadedFile("acta.pdf", b"%PDF-1.4\ncontenido", content_type="application/pdf")
                respuesta = self.client.post("/api/actas/", {
                    "expediente": expediente.pk, "archivo": archivo,
                }, format="multipart")
                self.assertEqual(respuesta.status_code, 201, respuesta.data)
                ids.append(respuesta.data["id"])
            cambio = self.client.patch(f"/api/actas/{ids[0]}/", {"expediente": segundo.pk}, format="multipart")
            self.assertEqual(cambio.status_code, 400, cambio.data)
            self.assertIn("expediente", cambio.data)

    def test_importacion_csv_informa_fila_con_columnas_extra(self):
        archivo = SimpleUploadedFile(
            "alumnos.csv", b"nombre,email,rut\nAlumno,alumno@example.com,,extra\n", content_type="text/csv"
        )
        respuesta = self.client.post("/api/cuentas/importar/", {"archivo": archivo}, format="multipart")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertIn("error", respuesta.data["resultados"][0])

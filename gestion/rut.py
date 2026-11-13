import re

from rest_framework import serializers


def normalizar_rut(valor):
    valor = valor.strip()
    if not (re.fullmatch(r"\d{1,2}\.\d{3}\.\d{3}-[\dKk]", valor)
            or re.fullmatch(r"\d{7,8}[\dKk]", valor)):
        raise serializers.ValidationError("Use un RUT como 12.345.678-5 o 123456785.")
    limpio = re.sub(r"[^0-9Kk]", "", valor).upper()
    if not re.fullmatch(r"[0-9]{7,8}[0-9K]", limpio):
        raise serializers.ValidationError("Ingrese un RUT chileno válido.")
    cuerpo, digito = limpio[:-1], limpio[-1]
    suma = sum(int(numero) * (2 + indice % 6) for indice, numero in enumerate(reversed(cuerpo)))
    resto = 11 - suma % 11
    esperado = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    if digito != esperado:
        raise serializers.ValidationError("El dígito verificador del RUT no coincide.")
    return f"{int(cuerpo):,}".replace(",", ".") + f"-{digito}"

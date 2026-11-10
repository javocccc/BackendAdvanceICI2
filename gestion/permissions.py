from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import PerfilUsuario


class PermisoExpediente(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        perfil = getattr(request.user, "perfil", None)
        if not perfil or not perfil.activo or not request.user.is_active:
            return False
        if request.method in SAFE_METHODS:
            return True
        return perfil.rol in (PerfilUsuario.Rol.ADMINISTRADOR, PerfilUsuario.Rol.COORDINADOR)

    def has_object_permission(self, request, view, obj):
        perfil = request.user.perfil
        if perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            return request.method in SAFE_METHODS and perfil.expediente_id == obj.id
        return True


class PermisoActa(PermisoExpediente):
    def has_object_permission(self, request, view, obj):
        perfil = request.user.perfil
        if perfil.rol == PerfilUsuario.Rol.ESTUDIANTE:
            return request.method in SAFE_METHODS and perfil.expediente_id == obj.expediente_id
        return True

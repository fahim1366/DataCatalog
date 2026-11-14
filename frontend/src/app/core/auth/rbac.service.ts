import { Injectable, computed, inject, signal } from '@angular/core';
import { AuthService } from './auth.service';

export enum PermissionAction {
  VIEW_METADATA = 'VIEW_METADATA',
  EDIT_DESCRIPTION = 'EDIT_DESCRIPTION',
  MANAGE_TAGS = 'MANAGE_TAGS',
  MANAGE_CONNECTIONS = 'MANAGE_CONNECTIONS',
  VIEW_AUDIT_LOGS = 'VIEW_AUDIT_LOGS',
  EXPORT_REPORTS = 'EXPORT_REPORTS',
}

export const ALL_PERMISSION_ACTIONS: PermissionAction[] = [
  PermissionAction.VIEW_METADATA,
  PermissionAction.EDIT_DESCRIPTION,
  PermissionAction.MANAGE_TAGS,
  PermissionAction.MANAGE_CONNECTIONS,
  PermissionAction.VIEW_AUDIT_LOGS,
  PermissionAction.EXPORT_REPORTS,
];

export const ROLE_PERMISSIONS: Record<string, string[]> = {
  SUPERADMIN: ALL_PERMISSION_ACTIONS,
  ADMIN: ALL_PERMISSION_ACTIONS,
  DATA_STEWARD: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.EDIT_DESCRIPTION,
    PermissionAction.MANAGE_TAGS,
    PermissionAction.EXPORT_REPORTS,
  ],
  DATASTEWARD: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.EDIT_DESCRIPTION,
    PermissionAction.MANAGE_TAGS,
    PermissionAction.EXPORT_REPORTS,
  ],
  DATA_ENGINEER: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.MANAGE_CONNECTIONS,
    PermissionAction.EXPORT_REPORTS,
  ],
  DATAENGINEER: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.MANAGE_CONNECTIONS,
    PermissionAction.EXPORT_REPORTS,
  ],
  ANALYST: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.EXPORT_REPORTS,
  ],
  DATAANALYST: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.EXPORT_REPORTS,
  ],
  DATA_ANALYST: [
    PermissionAction.VIEW_METADATA,
    PermissionAction.EXPORT_REPORTS,
  ],
  VIEWER: [
    PermissionAction.VIEW_METADATA,
  ],
  CATALOG_VIEWER: [
    PermissionAction.VIEW_METADATA,
  ],
};

@Injectable({
  providedIn: 'root',
})
export class RbacService {
  private readonly authService = inject(AuthService);
  private readonly _customPermissions = signal<Set<string>>(new Set());

  public readonly customPermissions = this._customPermissions.asReadonly();

  /**
   * Computed reactive signal containing all effective uppercase permission strings
   * resolved from superadmin status, explicit user permissions, assigned roles,
   * and custom overrides.
   */
  public readonly effectivePermissions = computed<Set<string>>(() => {
    const user = this.authService.currentUser();
    if (!user) {
      return new Set();
    }

    if (user.is_superadmin) {
      return new Set(ALL_PERMISSION_ACTIONS.map(action => action.toUpperCase()));
    }

    const resolved = new Set<string>();

    // Add permissions from user context (e.g. from token)
    if (Array.isArray(user.permissions)) {
      for (const perm of user.permissions) {
        if (typeof perm === 'string' && perm.trim()) {
          resolved.add(perm.trim().toUpperCase());
        }
      }
    }

    // Add permissions mapped from user roles
    if (Array.isArray(user.roles)) {
      for (const role of user.roles) {
        const normalizedRole = role?.trim().toUpperCase() ?? '';
        const rolePerms = ROLE_PERMISSIONS[normalizedRole];
        if (Array.isArray(rolePerms)) {
          for (const perm of rolePerms) {
            resolved.add(perm.toUpperCase());
          }
        }
      }
    }

    // Add dynamic custom permissions
    for (const perm of this._customPermissions()) {
      resolved.add(perm.toUpperCase());
    }

    return resolved;
  });

  /**
   * Checks whether the current user has the required permission (or all permissions if an array is provided).
   * Reactive when invoked inside an Angular effect() or computed().
   */
  public hasPermission(action: string | string[]): boolean {
    const user = this.authService.currentUser();
    if (!user) {
      return false;
    }

    if (user.is_superadmin) {
      return true;
    }

    const permissions = this.effectivePermissions();

    if (Array.isArray(action)) {
      if (action.length === 0) {
        return true;
      }
      return action.every(a => typeof a === 'string' && permissions.has(a.trim().toUpperCase()));
    }

    if (typeof action !== 'string' || !action.trim()) {
      return false;
    }

    return permissions.has(action.trim().toUpperCase());
  }

  /**
   * Checks whether the current user has ANY of the specified permissions.
   */
  public hasAnyPermission(actions: string[]): boolean {
    const user = this.authService.currentUser();
    if (!user) {
      return false;
    }

    if (user.is_superadmin) {
      return true;
    }

    if (!Array.isArray(actions) || actions.length === 0) {
      return false;
    }

    const permissions = this.effectivePermissions();
    return actions.some(a => typeof a === 'string' && permissions.has(a.trim().toUpperCase()));
  }

  /**
   * Checks whether the current user has a specific role or any role from the provided list.
   */
  public hasRole(role: string | string[]): boolean {
    const user = this.authService.currentUser();
    if (!user) {
      return false;
    }

    if (user.is_superadmin) {
      return true;
    }

    const userRoles = new Set((user.roles ?? []).map(r => r.toUpperCase()));

    if (Array.isArray(role)) {
      return role.some(r => userRoles.has(r.trim().toUpperCase()));
    }

    return userRoles.has(role.trim().toUpperCase());
  }

  /**
   * Sets custom dynamic permissions overriding or augmenting role-based permissions.
   */
  public setPermissions(permissions: string[]): void {
    const normalized = new Set(
      permissions
        .filter(p => typeof p === 'string' && p.trim().length > 0)
        .map(p => p.trim().toUpperCase())
    );
    this._customPermissions.set(normalized);
  }

  /**
   * Grants a single permission dynamically.
   */
  public grantPermission(permission: string): void {
    if (!permission || typeof permission !== 'string') {
      return;
    }
    const current = new Set(this._customPermissions());
    current.add(permission.trim().toUpperCase());
    this._customPermissions.set(current);
  }

  /**
   * Revokes a single dynamic permission.
   */
  public revokePermission(permission: string): void {
    if (!permission || typeof permission !== 'string') {
      return;
    }
    const current = new Set(this._customPermissions());
    current.delete(permission.trim().toUpperCase());
    this._customPermissions.set(current);
  }

  /**
   * Clears all custom permissions.
   */
  public clearPermissions(): void {
    this._customPermissions.set(new Set());
  }
}


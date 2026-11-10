import { inject } from '@angular/core';
import {
  ActivatedRouteSnapshot,
  CanActivateFn,
  Router,
  RouterStateSnapshot,
  UrlTree,
} from '@angular/router';
import { RbacService } from '../auth/rbac.service';

/**
 * Functional permission route guard enforcing RBAC authorization.
 *
 * Supports two usage patterns:
 * 1. Factory invocation in route config:
 *    `canActivate: [permissionGuard('EDIT_DESCRIPTION')]`
 *    `canActivate: [permissionGuard(['VIEW_METADATA', 'MANAGE_TAGS'])]`
 *
 * 2. Direct CanActivateFn registration relying on route data:
 *    `canActivate: [permissionGuard]`
 *    `data: { permission: 'EDIT_DESCRIPTION' }`
 */
export function permissionGuard(requiredAction: string | string[]): CanActivateFn;
export function permissionGuard(
  route: ActivatedRouteSnapshot,
  state: RouterStateSnapshot
): boolean | UrlTree;
export function permissionGuard(
  arg1: string | string[] | ActivatedRouteSnapshot,
  arg2?: RouterStateSnapshot
): CanActivateFn | boolean | UrlTree {
  if (typeof arg1 === 'string' || Array.isArray(arg1)) {
    const requiredAction = arg1;
    return (
      _route: ActivatedRouteSnapshot,
      _state: RouterStateSnapshot
    ): boolean | UrlTree => {
      const rbacService = inject(RbacService);
      const router = inject(Router);

      if (rbacService.hasPermission(requiredAction)) {
        return true;
      }
      return router.parseUrl('/unauthorized');
    };
  }

  // Invoked directly by the Angular Router as CanActivateFn
  const route = arg1 as ActivatedRouteSnapshot;
  const rbacService = inject(RbacService);
  const router = inject(Router);

  const requiredAction = (route.data?.['permission'] ??
    route.data?.['action'] ??
    route.data?.['requiredAction']) as string | string[] | undefined;

  if (!requiredAction || rbacService.hasPermission(requiredAction)) {
    return true;
  }

  return router.parseUrl('/unauthorized');
}


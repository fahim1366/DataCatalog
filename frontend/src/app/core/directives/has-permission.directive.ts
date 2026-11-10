import {
  Directive,
  Input,
  TemplateRef,
  ViewContainerRef,
  effect,
  inject,
  signal,
} from '@angular/core';
import { RbacService } from '../auth/rbac.service';

/**
 * Structural directive conditionally rendering DOM elements based on RBAC permissions.
 *
 * Driven reactively by Angular Signals:
 * Re-evaluates automatically when user authentication, role assignment, or input parameters change.
 *
 * Usage:
 * ```html
 * <button *hasPermission="'EDIT_DESCRIPTION'">Edit Table</button>
 *
 * <div *hasPermission="['VIEW_METADATA', 'MANAGE_TAGS']">Governance Controls</div>
 *
 * <div *hasPermission="'VIEW_AUDIT_LOGS'; else accessDenied">Audit Log</div>
 * <ng-template #accessDenied><span>Access Restricted</span></ng-template>
 * ```
 */
@Directive({
  selector: '[hasPermission]',
  standalone: true,
})
export class HasPermissionDirective {
  private readonly rbacService = inject(RbacService);
  private readonly templateRef = inject(TemplateRef<unknown>);
  private readonly viewContainer = inject(ViewContainerRef);

  private readonly _action = signal<string | string[]>('');
  private readonly _elseTemplate = signal<TemplateRef<unknown> | null>(null);

  private currentView: 'main' | 'else' | 'none' = 'none';

  @Input('hasPermission')
  set hasPermission(action: string | string[] | null | undefined) {
    this._action.set(action ?? '');
  }

  @Input('hasPermissionElse')
  set hasPermissionElse(template: TemplateRef<unknown> | null | undefined) {
    this._elseTemplate.set(template ?? null);
  }

  constructor() {
    effect(() => {
      const action = this._action();
      const elseTpl = this._elseTemplate();

      // Read effectivePermissions to establish reactive dependency on RBAC state
      const allowed = this.rbacService.hasPermission(action);

      if (allowed) {
        if (this.currentView !== 'main') {
          this.viewContainer.clear();
          this.viewContainer.createEmbeddedView(this.templateRef);
          this.currentView = 'main';
        }
      } else if (elseTpl) {
        if (this.currentView !== 'else') {
          this.viewContainer.clear();
          this.viewContainer.createEmbeddedView(elseTpl);
          this.currentView = 'else';
        }
      } else {
        if (this.currentView !== 'none') {
          this.viewContainer.clear();
          this.currentView = 'none';
        }
      }
    });
  }
}


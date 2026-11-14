import { Component, ChangeDetectionStrategy, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive, Router } from '@angular/router';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { AuthService } from './core/auth/auth.service';
import { RbacService } from './core/auth/rbac.service';
import { HasPermissionDirective } from './core/directives/has-permission.directive';
import { CatalogStore } from './features/catalog/stores/catalog.store';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    ScrollingModule,
    HasPermissionDirective,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  readonly authService = inject(AuthService);
  readonly rbacService = inject(RbacService);
  private readonly router = inject(Router);
  private readonly catalogStore = inject(CatalogStore);

  readonly title = signal<string>('Enterprise Data Catalog');
  readonly persianTitle = signal<string>('سامانه جامع کاتالوگ داده سازمانی');
  readonly isSidebarOpen = signal<boolean>(true);
  readonly currentVersion = signal<string>('1.0.0');

  readonly isRoleDropdownOpen = signal<boolean>(false);
  readonly isSwitchingRole = signal<boolean>(false);

  toggleSidebar(): void {
    this.isSidebarOpen.update((open) => !open);
  }

  toggleRoleDropdown(): void {
    this.isRoleDropdownOpen.update((open) => !open);
  }

  switchRole(roleType: 'admin' | 'steward' | 'analyst'): void {
    const creds = {
      admin: { username: 'admin', password: 'Admin#2026' },
      steward: { username: 'steward', password: 'Steward#2026' },
      analyst: { username: 'analyst', password: 'Analyst#2026' },
    }[roleType];

    this.isSwitchingRole.set(true);
    this.isRoleDropdownOpen.set(false);

    this.authService.login({ username_or_email: creds.username, password: creds.password }).subscribe({
      next: () => {
        this.isSwitchingRole.set(false);
        // Refresh catalog store to immediately reflect RBAC filter
        this.catalogStore.search('', true).catch(() => {});
        // Reload current active route so Trees, Connections, IAM, Audit Logs, and Reports refresh immediately
        const currentUrl = this.router.url;
        this.router.navigateByUrl('/', { skipLocationChange: true }).then(() => {
          this.router.navigateByUrl(currentUrl);
        });
      },
      error: () => {
        this.isSwitchingRole.set(false);
      },
    });
  }

  logout(): void {
    this.authService.logout();
    this.router.navigate(['/login']);
  }
}

// Alias for compatibility
export { AppComponent as App };

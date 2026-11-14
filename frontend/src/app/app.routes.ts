import { Routes } from '@angular/router';
import { CatalogExplorerComponent } from './features/catalog/catalog-explorer.component';
import { ConnectionListComponent } from './features/connections/connection-list.component';
import { TaxonomyManagementComponent } from './features/taxonomy/taxonomy-management.component';
import { VersionDiffComponent } from './features/versioning/version-diff.component';
import { IamManagementComponent } from './features/iam/iam-management.component';
import { AuditLogViewerComponent } from './features/audit/audit-log-viewer.component';
import { ExecutiveDashboardComponent } from './features/reports/executive-dashboard.component';
import { LoginComponent } from './features/auth/login.component';
import { authGuard } from './core/guards/auth.guard';
import { UserManagementComponent } from './features/access-control/users/user-management.component';
import { GroupManagementComponent } from './features/access-control/groups/group-management.component';
import { RoleManagementComponent } from './features/access-control/roles/role-management.component';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'catalog',
  },
  {
    path: 'login',
    component: LoginComponent,
    title: 'ورود به سامانه کاتالوگ داده',
  },
  {
    path: 'catalog',
    component: CatalogExplorerComponent,
    canActivate: [authGuard],
    title: 'کاوشگر کاتالوگ و درختواره داده - سامانه داده',
  },
  {
    path: 'connections',
    component: ConnectionListComponent,
    canActivate: [authGuard],
    title: 'مدیریت اتصالات و زمانبندی بروزرسانی - سامانه داده',
  },
  {
    path: 'taxonomy',
    component: TaxonomyManagementComponent,
    canActivate: [authGuard],
    title: 'مدیریت تگ‌ها و دسته‌بندی‌های موضوعی - سامانه داده',
  },
  {
    path: 'versions',
    component: VersionDiffComponent,
    canActivate: [authGuard],
    title: 'مقایسه نسخه‌ها و تغییرات اسکیما - سامانه داده',
  },
  {
    path: 'diff',
    redirectTo: 'versions',
    pathMatch: 'full',
  },
  {
    path: 'version-diff',
    redirectTo: 'versions',
    pathMatch: 'full',
  },
  {
    path: 'admin/users',
    component: UserManagementComponent,
    canActivate: [authGuard],
    title: 'مدیریت کاربران و سطوح دسترسی (User Management)',
  },
  {
    path: 'admin/groups',
    component: GroupManagementComponent,
    canActivate: [authGuard],
    title: 'مدیریت گروه‌های سازمانی (Group Management)',
  },
  {
    path: 'admin/roles',
    component: RoleManagementComponent,
    canActivate: [authGuard],
    title: 'مدیریت نقش‌ها و مجوزها (Role Management)',
  },
  {
    path: 'admin/iam',
    component: IamManagementComponent,
    canActivate: [authGuard],
    title: 'مدیریت کاربران، نقش‌ها و سطوح دسترسی (RBAC)',
  },
  {
    path: 'audit',
    component: AuditLogViewerComponent,
    canActivate: [authGuard],
    title: 'لاگ‌های ممیزی و امنیت (Audit Logs)',
  },
  {
    path: 'audit-logs',
    redirectTo: 'audit',
    pathMatch: 'full',
  },
  {
    path: 'reports',
    component: ExecutiveDashboardComponent,
    canActivate: [authGuard],
    title: 'داشبورد گزارشات مدیریتی - سامانه داده',
  },
  {
    path: 'dashboard',
    redirectTo: 'reports',
    pathMatch: 'full',
  },
  {
    path: '**',
    redirectTo: 'catalog',
  },
];

import { Routes } from '@angular/router';
import { CatalogExplorerComponent } from './features/catalog/catalog-explorer.component';
import { ConnectionListComponent } from './features/connections/connection-list.component';
import { TaxonomyManagementComponent } from './features/taxonomy/taxonomy-management.component';
import { VersionDiffComponent } from './features/versioning/version-diff.component';
import { IamManagementComponent } from './features/iam/iam-management.component';
import { AuditLogViewerComponent } from './features/audit/audit-log-viewer.component';
import { ExecutiveDashboardComponent } from './features/reports/executive-dashboard.component';
import { LoginComponent } from './features/auth/login.component';

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
    title: 'کاوشگر کاتالوگ و درختواره داده - سامانه داده',
  },
  {
    path: 'connections',
    component: ConnectionListComponent,
    title: 'مدیریت اتصالات و زمانبندی بروزرسانی - سامانه داده',
  },
  {
    path: 'taxonomy',
    component: TaxonomyManagementComponent,
    title: 'مدیریت تگ‌ها و دسته‌بندی‌های موضوعی - سامانه داده',
  },
  {
    path: 'versions',
    component: VersionDiffComponent,
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
    path: 'admin/iam',
    component: IamManagementComponent,
    title: 'مدیریت کاربران، نقش‌ها و سطوح دسترسی (RBAC)',
  },
  {
    path: 'audit',
    component: AuditLogViewerComponent,
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

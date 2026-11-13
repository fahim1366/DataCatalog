import { Routes } from '@angular/router';
import { TableVirtualScrollComponent } from './features/catalog/components/table-virtual-scroll/table-virtual-scroll.component';
import { ConnectionListComponent } from './features/connections/connection-list.component';
import { VersionDiffComponent } from './features/versioning/version-diff.component';
import { AuditLogViewerComponent } from './features/audit/audit-log-viewer.component';
import { ExecutiveDashboardComponent } from './features/reports/executive-dashboard.component';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'catalog',
  },
  {
    path: 'catalog',
    component: TableVirtualScrollComponent,
    title: 'Catalog Search - Enterprise Data Catalog',
  },
  {
    path: 'connections',
    component: ConnectionListComponent,
    title: 'Connections - Enterprise Data Catalog',
  },
  {
    path: 'diff',
    component: VersionDiffComponent,
    title: 'Version Diff Viewer - Enterprise Data Catalog',
  },
  {
    path: 'version-diff',
    redirectTo: 'diff',
    pathMatch: 'full',
  },
  {
    path: 'audit',
    component: AuditLogViewerComponent,
    title: 'Audit Logs - Enterprise Data Catalog',
  },
  {
    path: 'audit-logs',
    redirectTo: 'audit',
    pathMatch: 'full',
  },
  {
    path: 'reports',
    component: ExecutiveDashboardComponent,
    title: 'Executive Dashboard - Enterprise Data Catalog',
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

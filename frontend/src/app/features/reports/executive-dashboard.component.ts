import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { ReportService } from './services/report.service';
import { HasPermissionDirective } from '../../core/directives/has-permission.directive';
import {
  ClassificationReport,
  CompletenessReport,
  DatabaseCompletenessItem,
} from './models/report.models';
import { CompletenessGaugeComponent } from './completeness-gauge.component';
import { ClassificationSunburstComponent } from './classification-sunburst.component';

@Component({
  selector: 'app-executive-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    CompletenessGaugeComponent,
    ClassificationSunburstComponent,
    HasPermissionDirective,
  ],
  templateUrl: './executive-dashboard.component.html',
  styleUrls: ['./executive-dashboard.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExecutiveDashboardComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly reportService = inject(ReportService);

  readonly completenessReport = signal<CompletenessReport | null>(null);
  readonly classificationReport = signal<ClassificationReport | null>(null);
  readonly isLoading = signal<boolean>(false);
  readonly isRefreshing = signal<boolean>(false);
  readonly isExporting = signal<boolean>(false);
  readonly excludeLowRowTables = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly refreshSuccessMessage = signal<string | null>(null);

  // Available databases & categories for tree panel
  readonly availableDatabases = signal<{ id: string; name: string; engine_type: string; category_id?: string }[]>([]);
  readonly categoryTree = signal<any[]>([]);
  readonly activeReportTreeTab = signal<'databases' | 'categories'>('databases');
  readonly selectedConnectionIds = signal<Set<string>>(new Set());
  readonly treeSearchTerm = signal<string>('');
  readonly expandedCategoryIds = signal<Set<string>>(new Set());

  // Filtered databases for quick tree search
  readonly filteredDatabases = computed(() => {
    const term = this.treeSearchTerm().trim().toLowerCase();
    const dbs = this.availableDatabases();
    if (!term) return dbs;
    return dbs.filter(
      (db) =>
        db.name.toLowerCase().includes(term) ||
        (db.engine_type && db.engine_type.toLowerCase().includes(term))
    );
  });

  // Computed metrics for executive KPI cards
  readonly overallScore = computed<number>(
    () => this.completenessReport()?.overall_completeness_pct ?? 0
  );
  readonly totalDatabases = computed<number>(
    () => this.completenessReport()?.total_databases ?? 0
  );
  readonly totalTables = computed<number>(
    () => this.completenessReport()?.total_tables ?? 0
  );
  readonly totalColumns = computed<number>(
    () => this.completenessReport()?.total_columns ?? 0
  );
  readonly totalClassified = computed<number>(
    () => this.classificationReport()?.total_classified_columns ?? 0
  );
  readonly databaseBreakdown = computed<DatabaseCompletenessItem[]>(
    () => this.completenessReport()?.database_breakdown ?? []
  );

  ngOnInit(): void {
    this.loadTreeData();
    this.loadReports();
  }

  loadTreeData(): void {
    this.http.get<any[]>('/api/v1/connections').subscribe({
      next: (conns) => {
        this.availableDatabases.set(
          (conns || []).map((c) => ({
            id: c.id,
            name: c.name,
            engine_type: c.engine_type,
            category_id: c.category_id,
          }))
        );
      },
      error: () => {},
    });

    this.http.get<any[]>('/api/v1/categories/tree').subscribe({
      next: (cats) => {
        this.categoryTree.set(cats || []);
        // Expand top-level categories by default
        const topIds = new Set<string>((cats || []).map((c: any) => c.id));
        this.expandedCategoryIds.set(topIds);
      },
      error: () => {},
    });
  }

  loadDatabases(): void {
    this.loadTreeData();
  }

  onTreeSearchInput(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.treeSearchTerm.set(target ? target.value : '');
  }

  isDatabaseSelected(dbId: string): boolean {
    return this.selectedConnectionIds().has(dbId);
  }

  toggleDatabaseFilter(dbId: string): void {
    const current = new Set(this.selectedConnectionIds());
    if (current.has(dbId)) {
      current.delete(dbId);
    } else {
      current.add(dbId);
    }
    this.selectedConnectionIds.set(current);
  }

  isAllDatabasesSelected(): boolean {
    const dbs = this.availableDatabases();
    return dbs.length > 0 && this.selectedConnectionIds().size === dbs.length;
  }

  toggleSelectAllDatabases(): void {
    if (this.isAllDatabasesSelected()) {
      this.selectedConnectionIds.set(new Set());
    } else {
      const allIds = new Set(this.availableDatabases().map((d) => d.id));
      this.selectedConnectionIds.set(allIds);
    }
  }

  toggleCategoryDatabases(cat: any): void {
    const catDbIds = this.collectCategoryDbIds(cat);
    if (catDbIds.length === 0) return;

    const current = new Set(this.selectedConnectionIds());
    const allSelected = catDbIds.every((id) => current.has(id));

    if (allSelected) {
      catDbIds.forEach((id) => current.delete(id));
    } else {
      catDbIds.forEach((id) => current.add(id));
    }
    this.selectedConnectionIds.set(current);
  }

  isCategorySelected(cat: any): boolean {
    const catDbIds = this.collectCategoryDbIds(cat);
    if (catDbIds.length === 0) return false;
    return catDbIds.every((id) => this.selectedConnectionIds().has(id));
  }

  isCategoryIndeterminate(cat: any): boolean {
    const catDbIds = this.collectCategoryDbIds(cat);
    if (catDbIds.length === 0) return false;
    const selectedCount = catDbIds.filter((id) => this.selectedConnectionIds().has(id)).length;
    return selectedCount > 0 && selectedCount < catDbIds.length;
  }

  isCategoryExpanded(catId: string): boolean {
    return this.expandedCategoryIds().has(catId);
  }

  toggleCategoryExpand(catId: string, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    const current = new Set(this.expandedCategoryIds());
    if (current.has(catId)) {
      current.delete(catId);
    } else {
      current.add(catId);
    }
    this.expandedCategoryIds.set(current);
  }

  getCategoryDatabases(cat: any): { id: string; name: string; engine_type?: string }[] {
    const map = new Map<string, { id: string; name: string; engine_type?: string }>();
    if (cat.databases && Array.isArray(cat.databases)) {
      cat.databases.forEach((db: any) => {
        map.set(db.id, { id: db.id, name: db.name, engine_type: db.engine_type });
      });
    }
    this.availableDatabases().forEach((db) => {
      if (db.category_id === cat.id && !map.has(db.id)) {
        map.set(db.id, { id: db.id, name: db.name, engine_type: db.engine_type });
      }
    });
    return Array.from(map.values());
  }

  getCategoryDbCount(cat: any): number {
    return this.collectCategoryDbIds(cat).length;
  }

  private collectCategoryDbIds(cat: any): string[] {
    const ids = new Set<string>();
    if (cat.databases && Array.isArray(cat.databases)) {
      cat.databases.forEach((db: any) => ids.add(db.id));
    }
    this.availableDatabases().forEach((db) => {
      if (db.category_id === cat.id) {
        ids.add(db.id);
      }
    });
    if (cat.children && Array.isArray(cat.children)) {
      cat.children.forEach((child: any) => {
        this.collectCategoryDbIds(child).forEach((id) => ids.add(id));
      });
    }
    return Array.from(ids);
  }

  clearDatabaseFilter(): void {
    if (this.selectedConnectionIds().size > 0) {
      this.selectedConnectionIds.set(new Set());
      this.loadReports();
    }
  }

  generateReport(): void {
    this.loadReports();
  }

  toggleExcludeLowRowTables(): void {
    this.excludeLowRowTables.update((v) => !v);
    this.loadReports();
  }

  loadReports(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    const ids = Array.from(this.selectedConnectionIds());
    const excludeLowRows = this.excludeLowRowTables();

    const completeness$ = excludeLowRows
      ? this.reportService.getCompletenessReport(ids, true)
      : this.reportService.getCompletenessReport(ids);
    const classification$ = excludeLowRows
      ? this.reportService.getClassificationDistribution(ids, true)
      : this.reportService.getClassificationDistribution(ids);

    forkJoin({
      completeness: completeness$,
      classification: classification$,
    }).subscribe({
      next: ({ completeness, classification }) => {
        this.completenessReport.set(completeness);
        this.classificationReport.set(classification);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error ? err.message : 'خطا در بارگذاری گزارشات حاکمیت داده';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  refreshViews(): void {
    this.isRefreshing.set(true);
    this.refreshSuccessMessage.set(null);
    this.errorMessage.set(null);

    this.reportService.refreshMaterializedViews().subscribe({
      next: (response) => {
        this.refreshSuccessMessage.set(response.message);
        this.isRefreshing.set(false);
        this.loadReports();
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error
            ? err.message
            : 'Failed to refresh materialized views concurrently';
        this.errorMessage.set(msg);
        this.isRefreshing.set(false);
      },
    });
  }

  exportReport(format: 'json' | 'csv' | 'xlsx'): void {
    this.isExporting.set(true);
    const ids = Array.from(this.selectedConnectionIds());
    const excludeLowRows = this.excludeLowRowTables();
    const export$ = excludeLowRows
      ? this.reportService.exportReports(format, ids, true)
      : this.reportService.exportReports(format, ids);
    export$.subscribe({
      next: (blob) => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `enterprise_data_dictionary.${format}`;
        a.click();
        window.URL.revokeObjectURL(url);
        this.isExporting.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در دریافت خروجی گزارش';
        this.errorMessage.set(msg);
        this.isExporting.set(false);
      },
    });
  }
}


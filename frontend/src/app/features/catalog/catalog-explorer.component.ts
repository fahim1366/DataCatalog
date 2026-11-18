import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { CatalogStore } from './stores/catalog.store';
import {
  CategoryTreeNode,
  ChangeLogItem,
  ColumnItem,
  DescriptionPolicy,
  PhysicalDatabaseNode,
  SearchHit,
  TableDetail,
  TagItem,
} from './models/catalog.models';
import { InlineDescEditorComponent } from './components/inline-desc-editor/inline-desc-editor.component';
import { TagChipComponent } from './components/tag-chip/tag-chip.component';
import { DriftBannerComponent } from '../drift/drift-banner.component';
import { DriftResolutionDialogComponent } from '../drift/drift-resolution-dialog.component';
import { TableStatisticsChartComponent } from './table-statistics-chart.component';
import { HasPermissionDirective } from '../../core/directives/has-permission.directive';
import { RbacService } from '../../core/auth/rbac.service';
import { SyncJobService } from '../../core/services/sync-job.service';
import { ActivatedRoute } from '@angular/router';

@Component({
  selector: 'app-catalog-explorer',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ScrollingModule,
    InlineDescEditorComponent,
    TagChipComponent,
    DriftBannerComponent,
    DriftResolutionDialogComponent,
    TableStatisticsChartComponent,
    HasPermissionDirective,
  ],
  templateUrl: './catalog-explorer.component.html',
  styleUrls: ['./catalog-explorer.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CatalogExplorerComponent implements OnInit {
  readonly store = inject(CatalogStore);
  readonly rbac = inject(RbacService);
  readonly syncJobService = inject(SyncJobService);
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute, { optional: true });

  // Description Display Policy: 'APPROVED_FIRST' | 'SOURCE_FIRST' | 'BOTH'
  readonly descPolicy = signal<DescriptionPolicy>('APPROVED_FIRST');

  // Search & Filter State
  searchQuery = '';
  selectedDatabase = '';
  selectedSchema = '';
  selectedClassification = '';
  selectedStatus = '';
  selectedTag = '';
  selectedCategory = '';
  missingDescriptionsOnly = signal<boolean>(false);

  // Trees State
  activeTreeTab = signal<'physical' | 'category'>('physical');
  physicalTree = signal<PhysicalDatabaseNode[]>([]);
  categoryTree = signal<CategoryTreeNode[]>([]);
  isTreeLoading = signal<boolean>(false);
  isSidebarExpanded = signal<boolean>(true);

  // Multi-selection for bulk tagging
  selectedTableIds = signal<Set<string>>(new Set());

  // Table Detail Inspector Drawer
  selectedTable = signal<TableDetail | null>(null);
  selectedTableId = signal<string | null>(null);
  activeInspectorTab = signal<'columns' | 'metadata' | 'history' | 'statistics'>('columns');
  isInspectorLoading = signal<boolean>(false);
  tableHistory = signal<ChangeLogItem[]>([]);
  showDriftResolution = signal<boolean>(false);

  openDriftResolution(): void {
    this.showDriftResolution.set(true);
  }

  onDriftResolved(): void {
    const tid = this.selectedTableId();
    if (tid) {
      this.inspectTable(tid);
    }
  }

  // Bulk Ingestion Modal
  isIngestionModalOpen = signal<boolean>(false);
  ingestConnectionId = signal<string>('');
  ingestSchemaName = signal<string>('analytics_public');
  ingestJsonPayload = signal<string>(JSON.stringify([
    {
      name: 'fact_order_items',
      table_type: 'TABLE',
      row_count_estimate: 250000,
      size_bytes: 45000000,
      source_description: 'آیتم‌های فاکتور فروش استخراج شده از درگاه پرداخت',
      columns: [
        { name: 'item_id', ordinal_position: 1, data_type: 'BIGINT', raw_data_type: 'bigint', is_primary_key: true, classification_level: 'INTERNAL' },
        { name: 'order_id', ordinal_position: 2, data_type: 'BIGINT', raw_data_type: 'bigint', is_foreign_key: true, classification_level: 'INTERNAL' },
        { name: 'product_sku', ordinal_position: 3, data_type: 'VARCHAR(50)', raw_data_type: 'varchar(50)', classification_level: 'INTERNAL' },
        { name: 'unit_price_cents', ordinal_position: 4, data_type: 'NUMERIC(12,2)', raw_data_type: 'numeric(12,2)', classification_level: 'INTERNAL' }
      ]
    }
  ], null, 2));

  // Bulk Tag Modal
  isBulkTagModalOpen = signal<boolean>(false);
  availableTags = signal<TagItem[]>([]);
  selectedTagIdToAssign = signal<string>('');
  bulkTagAction = signal<'ASSIGN' | 'REMOVE'>('ASSIGN');

  // Tag Assignment to Column Modal
  isColumnTagModalOpen = signal<boolean>(false);
  targetColumn = signal<ColumnItem | null>(null);
  selectedTagForColumn = signal<string>('');

  // Toast Notification
  toastMessage = signal<string | null>(null);
  toastType = signal<'success' | 'error' | 'info'>('success');

  // Computed Selection
  hasSelectedTables = computed(() => this.selectedTableIds().size > 0);
  selectedCount = computed(() => this.selectedTableIds().size);

  // 4-Card Scope KPI Strip
  readonly kpiTotalTables = computed(() => this.store.searchHits().length);

  readonly kpiDocumentedPct = computed(() => {
    const hits = this.store.searchHits();
    if (hits.length === 0) return 0;
    const docCount = hits.filter(
      (h) => Boolean(h.approved_description?.trim() || h.source_description?.trim() || h.effective_description?.trim())
    ).length;
    return Math.round((docCount / hits.length) * 100);
  });

  readonly kpiSensitiveColumnsCount = computed(() => {
    const hits = this.store.searchHits();
    return hits.filter(
      (h) =>
        h.classification_level === 'PII' ||
        h.classification_level === 'CONFIDENTIAL' ||
        h.tags?.some((t) => {
          const lower = (t.name || '').toLowerCase();
          return lower.includes('pii') || lower.includes('confidential') || lower.includes('محرمانه');
        })
    ).length;
  });

  readonly kpiDriftCount = computed(() => {
    return this.store.searchHits().filter((h) => Boolean(h.description_drift_detected)).length;
  });

  ngOnInit(): void {
    // 0. Load persisted description display policy
    try {
      if (typeof localStorage !== 'undefined') {
        const savedPolicy = localStorage.getItem('datacatalog_desc_policy') as DescriptionPolicy;
        if (savedPolicy === 'APPROVED_FIRST' || savedPolicy === 'SOURCE_FIRST' || savedPolicy === 'BOTH') {
          this.descPolicy.set(savedPolicy);
        }
      }
    } catch {
      // Ignore in non-browser/restricted environments
    }

    // 1. Initial Catalog Load
    this.executeSearch();

    // 2. Query Params Sync (e.g. from top global search bar)
    if (this.route) {
      this.route.queryParams.subscribe((params) => {
        const q = params['q'];
        if (q !== undefined && q !== this.searchQuery) {
          this.searchQuery = q;
          this.executeSearch();
        }
      });
    }

    // 3. Load Hierarchy Trees
    this.loadTrees();

    // 4. Load Available Tags
    this.loadAvailableTags();
  }

  setDescPolicy(policy: DescriptionPolicy): void {
    this.descPolicy.set(policy);
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('datacatalog_desc_policy', policy);
      }
    } catch {}
  }

  triggerDatabaseSync(conn: PhysicalDatabaseNode, event: MouseEvent): void {
    event.stopPropagation();
    this.syncJobService.triggerSync(conn.id).subscribe({
      next: () => {
        this.showToast(`همگام‌سازی متادیتا برای پایگاه داده «${conn.name}» آغاز شد.`, 'success');
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : `خطا در آغاز همگام‌سازی پایگاه داده «${conn.name}»`;
        this.showToast(msg, 'error');
      },
    });
  }

  navigateToAll(): void {
    this.selectedDatabase = '';
    this.selectedSchema = '';
    this.executeSearch();
  }

  navigateToDatabase(dbName: string): void {
    this.selectedDatabase = dbName;
    this.selectedSchema = '';
    this.executeSearch();
  }

  getDisplayDescription(hit: SearchHit): {
    policy: DescriptionPolicy;
    primaryText: string;
    origin: 'approved' | 'source' | 'none';
    secondaryText?: string;
    secondaryOrigin?: 'approved' | 'source';
  } {
    const policy = this.descPolicy();
    const app = hit.approved_description?.trim();
    const src = hit.source_description?.trim();
    const eff = hit.effective_description?.trim();

    if (policy === 'APPROVED_FIRST') {
      if (app) {
        return { policy, primaryText: app, origin: 'approved' };
      }
      if (src) {
        return { policy, primaryText: src, origin: 'source' };
      }
      if (eff) {
        return { policy, primaryText: eff, origin: 'source' };
      }
      return { policy, primaryText: 'بدون توضیحات ثبت شده', origin: 'none' };
    }

    if (policy === 'SOURCE_FIRST') {
      if (src) {
        return { policy, primaryText: src, origin: 'source' };
      }
      if (app) {
        return { policy, primaryText: app, origin: 'approved' };
      }
      if (eff) {
        return { policy, primaryText: eff, origin: 'source' };
      }
      return { policy, primaryText: 'بدون توضیحات ثبت شده', origin: 'none' };
    }

    // BOTH
    if (app && src) {
      return {
        policy,
        primaryText: app,
        origin: 'approved',
        secondaryText: src,
        secondaryOrigin: 'source',
      };
    }
    if (app) {
      return { policy, primaryText: app, origin: 'approved' };
    }
    if (src) {
      return { policy, primaryText: src, origin: 'source' };
    }
    if (eff) {
      return { policy, primaryText: eff, origin: 'source' };
    }
    return { policy, primaryText: 'بدون توضیحات ثبت شده', origin: 'none' };
  }

  executeSearch(): void {
    const filters = {
      database: this.selectedDatabase || undefined,
      schema: this.selectedSchema || undefined,
      classification: this.selectedClassification || undefined,
      status: this.selectedStatus || undefined,
      tag: this.selectedTag || undefined,
      category: this.selectedCategory || undefined,
      missing_descriptions_only: this.missingDescriptionsOnly() || undefined,
    };
    this.store.search(this.searchQuery, true, filters).catch(() => {
      this.showToast('خطا در برقراری ارتباط با سرور متادیتا', 'error');
    });
  }

  toggleMissingDescriptionsOnly(): void {
    this.missingDescriptionsOnly.update((v) => !v);
    this.executeSearch();
  }

  resetFilters(): void {
    this.searchQuery = '';
    this.selectedDatabase = '';
    this.selectedSchema = '';
    this.selectedClassification = '';
    this.selectedStatus = '';
    this.selectedTag = '';
    this.selectedCategory = '';
    this.missingDescriptionsOnly.set(false);
    this.executeSearch();
  }

  clearSelection(): void {
    this.selectedTableIds.set(new Set());
  }

  async loadTrees(): Promise<void> {
    this.isTreeLoading.set(true);
    try {
      const [phys, cat] = await Promise.all([
        this.store.getPhysicalTree(),
        this.store.getCategoryTree(),
      ]);
      this.physicalTree.set(phys);
      this.categoryTree.set(cat);
      if (phys.length > 0 && !this.ingestConnectionId()) {
        this.ingestConnectionId.set(phys[0].id);
      }
    } catch {
      // Ignored or handled gracefully
    } finally {
      this.isTreeLoading.set(false);
    }
  }

  loadAvailableTags(): void {
    this.http.get<TagItem[]>('/api/v1/tags').subscribe({
      next: (tags) => {
        this.availableTags.set(tags);
        if (tags.length > 0 && !this.selectedTagIdToAssign()) {
          this.selectedTagIdToAssign.set(tags[0].id);
        }
      },
      error: () => {},
    });
  }

  // Tree Node Selection Handlers
  filterByDatabase(databaseName: string): void {
    this.selectedDatabase = databaseName;
    this.selectedSchema = '';
    this.executeSearch();
  }

  filterBySchema(schemaName: string): void {
    this.selectedSchema = schemaName;
    this.executeSearch();
  }

  filterByCategory(categoryIdentifier: string): void {
    this.selectedCategory = categoryIdentifier;
    this.executeSearch();
  }

  filterByTable(tableName: string): void {
    this.searchQuery = tableName;
    this.executeSearch();
  }

  // Selection Checkbox Handlers
  toggleTableSelection(id: string): void {
    this.selectedTableIds.update((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  toggleSelectAll(): void {
    const hits = this.store.searchHits();
    const current = this.selectedTableIds();
    if (current.size === hits.length && hits.length > 0) {
      this.selectedTableIds.set(new Set());
    } else {
      this.selectedTableIds.set(new Set(hits.map((h) => h.id)));
    }
  }

  isTableSelected(id: string): boolean {
    return this.selectedTableIds().has(id);
  }

  // Table Detail Inspector Drawer
  async inspectTable(tableId: string): Promise<void> {
    this.selectedTableId.set(tableId);
    this.isInspectorLoading.set(true);
    try {
      const detail = await this.store.getTableDetails(tableId);
      this.selectedTable.set(detail);
      const history = await this.store.getTableHistory(tableId);
      this.tableHistory.set(history);
    } catch {
      this.showToast('عدم دسترسی یا خطا در بارگذاری شناسنامه جدول', 'error');
      this.selectedTable.set(null);
    } finally {
      this.isInspectorLoading.set(false);
    }
  }

  closeInspector(): void {
    this.selectedTable.set(null);
    this.selectedTableId.set(null);
  }

  // AI Suggestion Approvals
  async approveAiTableDescription(tableId: string): Promise<void> {
    try {
      const updated = await this.store.approveAiTableDescription(tableId);
      this.selectedTable.set(updated);
      const history = await this.store.getTableHistory(tableId);
      this.tableHistory.set(history);
      this.showToast('پیشنهاد هوش مصنوعی با موفقیت تایید و به عنوان شرح نهایی ثبت شد', 'success');
    } catch {
      this.showToast('خطا در تایید پیشنهاد هوش مصنوعی', 'error');
    }
  }

  async approveAiColumnDescription(col: ColumnItem): Promise<void> {
    try {
      const updated = await this.store.approveAiColumnDescription(col.id);
      this.selectedTable.update((curr) => {
        if (!curr) return null;
        return {
          ...curr,
          columns: curr.columns.map((c) => (c.id === col.id ? updated : c)),
        };
      });
      const history = await this.store.getTableHistory(col.table_id);
      this.tableHistory.set(history);
      this.showToast(`پیشنهاد هوش مصنوعی برای ستون «${col.name}» تایید گردید`, 'success');
    } catch {
      this.showToast('خطا در تایید پیشنهاد هوش مصنوعی ستون', 'error');
    }
  }

  // Table Approved Description Editor
  async onSaveTableDescription(newDesc: string): Promise<void> {
    const tbl = this.selectedTable();
    if (!tbl) return;
    try {
      await this.store.updateDescription(tbl.id, newDesc);
      this.selectedTable.update((curr) => (curr ? { ...curr, approved_description: newDesc, effective_description: newDesc } : curr));
      const history = await this.store.getTableHistory(tbl.id);
      this.tableHistory.set(history);
      this.showToast('توضیحات جدول با موفقیت ذخیره و در زنجیره تغییرات ثبت شد', 'success');
    } catch {
      this.showToast('خطا در ذخیره توضیحات جدول', 'error');
    }
  }

  // Column Approved Description Editor
  async onSaveColumnDescription(col: ColumnItem, newDesc: string): Promise<void> {
    try {
      const updated = await this.store.updateColumnDescription(col.id, newDesc);
      this.selectedTable.update((curr) => {
        if (!curr) return null;
        return {
          ...curr,
          columns: curr.columns.map((c) => (c.id === col.id ? updated : c)),
        };
      });
      const history = await this.store.getTableHistory(col.table_id);
      this.tableHistory.set(history);
      this.showToast(`توضیحات فیلد ${col.name} ذخیره شد`, 'success');
    } catch {
      this.showToast('خطا در ذخیره توضیحات ستون', 'error');
    }
  }

  // Column Classification Dropdown Update
  async onClassificationChange(col: ColumnItem, newLevel: string): Promise<void> {
    try {
      const updated = await this.store.updateColumnClassification(col.id, newLevel);
      this.selectedTable.update((curr) => {
        if (!curr) return null;
        return {
          ...curr,
          columns: curr.columns.map((c) => (c.id === col.id ? updated : c)),
        };
      });
      const history = await this.store.getTableHistory(col.table_id);
      this.tableHistory.set(history);
      this.showToast(`سطح محرمانگی فیلد ${col.name} به ${newLevel} تغییر یافت`, 'success');
    } catch {
      this.showToast('خطا در تغییر سطح محرمانگی', 'error');
    }
  }

  // Column Tag Assignment
  openColumnTagModal(col: ColumnItem): void {
    this.targetColumn.set(col);
    if (this.availableTags().length > 0) {
      this.selectedTagForColumn.set(this.availableTags()[0].id);
    }
    this.isColumnTagModalOpen.set(true);
  }

  closeColumnTagModal(): void {
    this.isColumnTagModalOpen.set(false);
    this.targetColumn.set(null);
  }

  async assignTagToColumn(): Promise<void> {
    const col = this.targetColumn();
    const tagId = this.selectedTagForColumn();
    if (!col || !tagId) return;

    try {
      await this.store.bulkTag({
        tag_ids: [tagId],
        action: 'ASSIGN',
        targets: [{ entity_type: 'COLUMN', entity_id: col.id }],
      });
      this.showToast('تگ با موفقیت به فیلد تخصیص داده شد', 'success');
      this.closeColumnTagModal();
      // Reload table details to reflect tags
      const detail = await this.store.getTableDetails(col.table_id);
      this.selectedTable.set(detail);
    } catch {
      this.showToast('خطا در تخصیص تگ به فیلد', 'error');
    }
  }

  async removeTagFromColumn(col: ColumnItem, tag: TagItem): Promise<void> {
    try {
      await this.store.bulkTag({
        tag_ids: [tag.id],
        action: 'REMOVE',
        targets: [{ entity_type: 'COLUMN', entity_id: col.id }],
      });
      this.showToast(`تگ ${tag.name} از فیلد حذف شد`, 'info');
      const detail = await this.store.getTableDetails(col.table_id);
      this.selectedTable.set(detail);
    } catch {
      this.showToast('خطا در حذف تگ', 'error');
    }
  }

  // Bulk Tagging Handlers
  openBulkTagModal(): void {
    this.isBulkTagModalOpen.set(true);
  }

  closeBulkTagModal(): void {
    this.isBulkTagModalOpen.set(false);
  }

  async executeBulkTag(): Promise<void> {
    const tableIds = Array.from(this.selectedTableIds());
    const tagId = this.selectedTagIdToAssign();
    if (tableIds.length === 0 || !tagId) return;

    try {
      const res = await this.store.bulkTag({
        tag_ids: [tagId],
        action: this.bulkTagAction(),
        targets: tableIds.map((tid) => ({ entity_type: 'TABLE', entity_id: tid })),
      });
      this.showToast(`عملیات تگ روی ${res.affected_count} جدول با موفقیت انجام شد`, 'success');
      this.closeBulkTagModal();
      this.selectedTableIds.set(new Set());
      this.executeSearch();
    } catch {
      this.showToast('خطا در اجرای تخصیص گروهی تگ', 'error');
    }
  }

  // Bulk Ingestion Handlers
  openIngestionModal(): void {
    this.isIngestionModalOpen.set(true);
  }

  closeIngestionModal(): void {
    this.isIngestionModalOpen.set(false);
  }

  async executeBulkIngestion(): Promise<void> {
    try {
      const tablesParsed = JSON.parse(this.ingestJsonPayload());
      const payload = {
        connection_id: this.ingestConnectionId(),
        schema_name: this.ingestSchemaName(),
        tables: tablesParsed,
      };

      await this.http.post('/api/v1/ingestion/bulk', payload).toPromise();
      this.showToast('بارگذاری متادیتا با موفقیت ثبت شد و نسخه اسکیما ارتقا یافت', 'success');
      this.closeIngestionModal();
      this.executeSearch();
      this.loadTrees();
    } catch (err: any) {
      const msg = err?.error?.detail || 'خطا در بارگذاری ساختار متادیتا. از معتبر بودن ساختار JSON اطمینان حاصل فرمایید.';
      this.showToast(msg, 'error');
    }
  }

  // Toast Helper
  showToast(message: string, type: 'success' | 'error' | 'info' = 'success'): void {
    this.toastMessage.set(message);
    this.toastType.set(type);
    setTimeout(() => {
      this.toastMessage.set(null);
    }, 4000);
  }

  trackByHitId(index: number, hit: SearchHit): string {
    return hit.id;
  }
}

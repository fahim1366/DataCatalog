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
  PhysicalDatabaseNode,
  SearchHit,
  TableDetail,
  TagItem,
} from './models/catalog.models';
import { InlineDescEditorComponent } from './components/inline-desc-editor/inline-desc-editor.component';
import { TagChipComponent } from './components/tag-chip/tag-chip.component';
import { HasPermissionDirective } from '../../core/directives/has-permission.directive';
import { RbacService } from '../../core/auth/rbac.service';

@Component({
  selector: 'app-catalog-explorer',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ScrollingModule,
    InlineDescEditorComponent,
    TagChipComponent,
    HasPermissionDirective,
  ],
  templateUrl: './catalog-explorer.component.html',
  styleUrls: ['./catalog-explorer.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CatalogExplorerComponent implements OnInit {
  readonly store = inject(CatalogStore);
  readonly rbac = inject(RbacService);
  private readonly http = inject(HttpClient);

  // Search & Filter State
  searchQuery = '';
  selectedDatabase = '';
  selectedSchema = '';
  selectedClassification = '';
  selectedStatus = '';
  selectedTag = '';
  selectedCategory = '';

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
  activeInspectorTab = signal<'columns' | 'metadata' | 'history'>('columns');
  isInspectorLoading = signal<boolean>(false);
  tableHistory = signal<ChangeLogItem[]>([]);

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

  // Computed
  hasSelectedTables = computed(() => this.selectedTableIds().size > 0);
  selectedCount = computed(() => this.selectedTableIds().size);

  ngOnInit(): void {
    // 1. Initial Catalog Load
    this.executeSearch();

    // 2. Load Hierarchy Trees
    this.loadTrees();

    // 3. Load Available Tags
    this.loadAvailableTags();
  }

  executeSearch(): void {
    const filters = {
      database: this.selectedDatabase || undefined,
      schema: this.selectedSchema || undefined,
      classification: this.selectedClassification || undefined,
      status: this.selectedStatus || undefined,
      tag: this.selectedTag || undefined,
      category: this.selectedCategory || undefined,
    };
    this.store.search(this.searchQuery, true, filters).catch(() => {
      this.showToast('خطا در برقراری ارتباط با سرور متادیتا', 'error');
    });
  }

  resetFilters(): void {
    this.searchQuery = '';
    this.selectedDatabase = '';
    this.selectedSchema = '';
    this.selectedClassification = '';
    this.selectedStatus = '';
    this.selectedTag = '';
    this.selectedCategory = '';
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

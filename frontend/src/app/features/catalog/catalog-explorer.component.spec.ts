import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { of } from 'rxjs';
import { CatalogExplorerComponent } from './catalog-explorer.component';
import { CatalogStore } from './stores/catalog.store';
import { SyncJobService } from '../../core/services/sync-job.service';
import { RbacService } from '../../core/auth/rbac.service';
import { SearchHit } from './models/catalog.models';

describe('CatalogExplorerComponent (Phase 2 UI/UX Overhaul)', () => {
  let fixture: ComponentFixture<CatalogExplorerComponent>;
  let component: CatalogExplorerComponent;
  let syncJobService: SyncJobService;
  let store: CatalogStore;

  const mockStorage: Record<string, string> = {};
  const fakeLocalStorage = {
    getItem: (key: string) => mockStorage[key] ?? null,
    setItem: (key: string, val: string) => { mockStorage[key] = val; },
    removeItem: (key: string) => { delete mockStorage[key]; },
    clear: () => { for (const k in mockStorage) delete mockStorage[k]; },
    key: () => null,
    length: 0,
  };

  beforeAll(() => {
    Object.defineProperty(globalThis, 'localStorage', {
      value: fakeLocalStorage,
      writable: true,
      configurable: true,
    });
  });

  const mockHits: SearchHit[] = [
    {
      id: 'table-1',
      entity_type: 'TABLE',
      name: 'users_table',
      database_name: 'prod_db',
      schema_name: 'core',
      source_description: 'Source DB users',
      approved_description: 'Approved curated users',
      effective_description: 'Approved curated users',
      classification_level: 'PII',
      status: 'ACTIVE',
      row_count_estimate: 15000,
      size_bytes: 5242880,
      description_drift_detected: true,
      tags: [{ id: 'tag-1', name: 'PII', color: '#ff0000' }],
    },
    {
      id: 'table-2',
      entity_type: 'TABLE',
      name: 'orders_table',
      database_name: 'prod_db',
      schema_name: 'core',
      source_description: 'Raw orders data',
      approved_description: null,
      effective_description: 'Raw orders data',
      classification_level: 'INTERNAL',
      status: 'ACTIVE',
      row_count_estimate: 80000,
      size_bytes: 20971520,
      description_drift_detected: false,
    },
    {
      id: 'table-3',
      entity_type: 'TABLE',
      name: 'logs_table',
      database_name: 'dw_db',
      schema_name: 'audit',
      source_description: null,
      approved_description: null,
      effective_description: null,
      classification_level: 'PUBLIC',
      status: 'ACTIVE',
      row_count_estimate: 200,
      size_bytes: 1024,
      description_drift_detected: false,
    },
  ];

  beforeEach(async () => {
    fakeLocalStorage.clear();

    await TestBed.configureTestingModule({
      imports: [CatalogExplorerComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        CatalogStore,
        SyncJobService,
        {
          provide: RbacService,
          useValue: {
            hasPermission: () => true,
            currentUser: () => ({ is_superadmin: true }),
          },
        },
      ],
    }).compileComponents();

    syncJobService = TestBed.inject(SyncJobService);
    store = TestBed.inject(CatalogStore);

    vi.spyOn(store, 'search').mockImplementation(async () => {
      (store as any)._searchHits.set(mockHits);
    });
    vi.spyOn(store, 'getPhysicalTree').mockResolvedValue([]);
    vi.spyOn(store, 'getCategoryTree').mockResolvedValue([]);
    vi.spyOn(store, 'getCatalogObjects').mockResolvedValue({
      counts: { tables: 3, views: 0, indexes: 0, procedures: 0, functions: 0 },
      tables: [],
      views: [],
      indexes: [],
      procedures: [],
      functions: [],
    });

    fixture = TestBed.createComponent(CatalogExplorerComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fakeLocalStorage.clear();
  });

  it('should create the CatalogExplorerComponent instance with default BOTH policy', () => {
    expect(component).toBeTruthy();
    expect(component.descPolicy()).toBe('BOTH');
  });

  it('should persist and load description policy from localStorage', () => {
    component.setDescPolicy('SOURCE_FIRST');
    expect(component.descPolicy()).toBe('SOURCE_FIRST');
    expect(localStorage.getItem('datacatalog_desc_policy')).toBe('SOURCE_FIRST');

    component.setDescPolicy('BOTH');
    expect(component.descPolicy()).toBe('BOTH');
    expect(localStorage.getItem('datacatalog_desc_policy')).toBe('BOTH');
  });

  it('should resolve display description according to APPROVED_FIRST policy', () => {
    component.setDescPolicy('APPROVED_FIRST');

    // Table 1 has approved_description
    const desc1 = component.getDisplayDescription(mockHits[0]);
    expect(desc1.primaryText).toBe('Approved curated users');
    expect(desc1.origin).toBe('approved');

    // Table 2 has only source_description
    const desc2 = component.getDisplayDescription(mockHits[1]);
    expect(desc2.primaryText).toBe('Raw orders data');
    expect(desc2.origin).toBe('source');

    // Table 3 has no description
    const desc3 = component.getDisplayDescription(mockHits[2]);
    expect(desc3.primaryText).toBe('بدون توضیحات ثبت شده');
    expect(desc3.origin).toBe('none');
  });

  it('should resolve display description according to SOURCE_FIRST policy', () => {
    component.setDescPolicy('SOURCE_FIRST');

    // Table 1 prefers source_description
    const desc1 = component.getDisplayDescription(mockHits[0]);
    expect(desc1.primaryText).toBe('Source DB users');
    expect(desc1.origin).toBe('source');
  });

  it('should resolve display description according to BOTH policy', () => {
    component.setDescPolicy('BOTH');

    // Table 1 has both descriptions
    const desc1 = component.getDisplayDescription(mockHits[0]);
    expect(desc1.primaryText).toBe('Approved curated users');
    expect(desc1.origin).toBe('approved');
    expect(desc1.secondaryText).toBe('Source DB users');
    expect(desc1.secondaryOrigin).toBe('source');
  });

  it('should correctly calculate the 4 KPI strip metrics reflecting the scope', async () => {
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.kpiTotalTables()).toBe(3);
    // 2 out of 3 tables documented = 67%
    expect(component.kpiDocumentedPct()).toBe(67);
    // 1 table has PII classification
    expect(component.kpiSensitiveColumnsCount()).toBe(1);
    // 1 table has drift detected
    expect(component.kpiDriftCount()).toBe(1);
  });

  it('should update scope with interactive breadcrumb navigation', () => {
    component.selectedDatabase = 'prod_db';
    component.selectedSchema = 'core';

    component.navigateToDatabase('prod_db');
    expect(component.selectedDatabase).toBe('prod_db');
    expect(component.selectedSchema).toBe('');

    component.navigateToAll();
    expect(component.selectedDatabase).toBe('');
    expect(component.selectedSchema).toBe('');
  });

  it('should trigger inline database sync via SyncJobService with toast notification', () => {
    const triggerSpy = vi.spyOn(syncJobService, 'triggerSync').mockReturnValue(of({} as any));
    const mockConn = {
      id: 'conn-123',
      name: 'crm_postgres',
      database_name: 'crm_db',
      engine_type: 'POSTGRESQL',
      schemas: [],
    };
    const event = new MouseEvent('click');
    const stopSpy = vi.spyOn(event, 'stopPropagation');

    component.triggerDatabaseSync(mockConn, event);

    expect(stopSpy).toHaveBeenCalled();
    expect(triggerSpy).toHaveBeenCalledWith('conn-123');
    expect(component.toastMessage()).toContain('همگام‌سازی متادیتا برای پایگاه داده «crm_postgres» آغاز شد.');
  });

  it('should start editing, save table owner, and update reactive state', async () => {
    component.selectedTable.set({
      id: 'tbl-123',
      schema_id: 'sch-1',
      database_name: 'prod_db',
      schema_name: 'public',
      name: 'customers',
      table_type: 'BASE TABLE',
      status: 'ACTIVE',
      owner: 'old_owner',
      row_count_estimate: 1000,
      size_bytes: 2048,
      columns: [],
      tags: [],
    } as any);

    component.startEditingOwner();
    expect(component.isEditingOwner()).toBe(true);
    expect(component.editOwnerValue()).toBe('old_owner');

    component.editOwnerValue.set('steward_team@company.internal');

    const updateSpy = vi.spyOn(store, 'updateTableOwner').mockResolvedValue({
      id: 'tbl-123',
      owner: 'steward_team@company.internal',
    } as any);
    const historySpy = vi.spyOn(store, 'getTableHistory').mockResolvedValue([]);

    await component.saveTableOwner();

    expect(updateSpy).toHaveBeenCalledWith('tbl-123', 'steward_team@company.internal');
    expect(component.selectedTable()?.owner).toBe('steward_team@company.internal');
    expect(component.isEditingOwner()).toBe(false);
    expect(component.toastMessage()).toContain('مالک جدول با موفقیت ذخیره');
  });

  it('should dynamically update first KPI card label, icon, and count based on active object tab', () => {
    component.catalogObjects.set({
      counts: { tables: 3, views: 5, indexes: 8, procedures: 2, functions: 4 },
      tables: [],
      views: [
        { id: 'v1', name: 'v_users', object_type: 'VIEW' } as any,
        { id: 'v2', name: 'v_orders', object_type: 'VIEW' } as any,
      ],
      indexes: [
        { id: 'idx1', name: 'idx_users_id', object_type: 'INDEX' } as any,
        { id: 'idx2', name: 'idx_orders_id', object_type: 'INDEX' } as any,
        { id: 'idx3', name: 'idx_logs_id', object_type: 'INDEX' } as any,
      ],
      procedures: [
        { id: 'sp1', name: 'sp_sync', object_type: 'PROCEDURE' } as any,
      ],
      functions: [
        { id: 'fn1', name: 'fn_calc', object_type: 'FUNCTION' } as any,
        { id: 'fn2', name: 'fn_tax', object_type: 'FUNCTION' } as any,
      ],
    });

    // Default: tables
    component.setActiveObjectTab('tables');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل جداول');
    expect(component.kpiDynamicIcon()).toBe('pi pi-table');
    expect(component.kpiDynamicCount()).toBe(3); // from searchHits

    // Views
    component.setActiveObjectTab('views');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل نماها');
    expect(component.kpiDynamicIcon()).toBe('pi pi-eye');
    expect(component.kpiDynamicCount()).toBe(2);

    // Indexes
    component.setActiveObjectTab('indexes');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل ایندکس‌ها');
    expect(component.kpiDynamicIcon()).toBe('pi pi-bolt');
    expect(component.kpiDynamicCount()).toBe(3);

    // Procedures
    component.setActiveObjectTab('procedures');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل روال‌های ذخیره');
    expect(component.kpiDynamicIcon()).toBe('pi pi-cog');
    expect(component.kpiDynamicCount()).toBe(1);

    // Functions
    component.setActiveObjectTab('functions');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل توابع');
    expect(component.kpiDynamicIcon()).toBe('pi pi-code');
    expect(component.kpiDynamicCount()).toBe(2);
  });

  it('should filter and display indexes specifically belonging to selectedTable().id in inspector', () => {
    component.selectedTable.set({
      id: 'tbl-target',
      name: 'customers',
      columns: [],
      tags: [],
    } as any);

    component.catalogObjects.set({
      counts: { tables: 1, views: 0, indexes: 3, procedures: 0, functions: 0 },
      tables: [],
      views: [],
      indexes: [
        {
          id: 'idx-1',
          name: 'idx_customers_email',
          table_name: 'customers',
          details: {
            table_id: 'tbl-target',
            index_type: 'BTREE',
            is_unique: true,
            is_primary: false,
            columns_list: ['email'],
          },
        } as any,
        {
          id: 'idx-2',
          name: 'pk_customers',
          table_name: 'customers',
          details: {
            table_id: 'tbl-target',
            index_type: 'BTREE',
            is_unique: true,
            is_primary: true,
            columns_list: ['id'],
          },
        } as any,
        {
          id: 'idx-3',
          name: 'idx_orders_customer_id',
          table_name: 'orders',
          details: {
            table_id: 'tbl-other',
            index_type: 'GIN',
            is_unique: false,
            is_primary: false,
            columns_list: ['customer_id'],
          },
        } as any,
      ],
      procedures: [],
      functions: [],
    });

    component.activeInspectorTab.set('indexes');
    expect(component.activeInspectorTab()).toBe('indexes');

    const indexes = component.tableIndexes();
    expect(indexes.length).toBe(2);
    expect(indexes[0].name).toBe('idx_customers_email');
    expect(indexes[0].details?.['index_type']).toBe('BTREE');
    expect(indexes[0].details?.['is_unique']).toBe(true);
    expect(indexes[0].details?.['is_primary']).toBe(false);
    expect(indexes[0].details?.['columns_list']).toEqual(['email']);

    expect(indexes[1].name).toBe('pk_customers');
    expect(indexes[1].details?.['is_primary']).toBe(true);
  });

  it('should render Collation column in columns table drawer and format text/non-text correctly', async () => {
    component.selectedTable.set({
      id: 'tbl-collate',
      schema_id: 's1',
      schema_name: 'public',
      database_name: 'prod_db',
      name: 'customers',
      table_type: 'TABLE',
      row_count_estimate: 100,
      size_bytes: 4096,
      status: 'ACTIVE',
      columns: [
        {
          id: 'c1',
          table_id: 'tbl-collate',
          name: 'id',
          ordinal_position: 1,
          data_type: 'uuid',
          raw_data_type: 'uuid',
          collation: null,
          is_nullable: false,
          is_primary_key: true,
          is_foreign_key: false,
          classification_level: 'INTERNAL',
          status: 'ACTIVE',
        },
        {
          id: 'c2',
          table_id: 'tbl-collate',
          name: 'title_fa',
          ordinal_position: 2,
          data_type: 'varchar(255)',
          raw_data_type: 'varchar',
          collation: 'fa_IR.utf8',
          is_nullable: true,
          is_primary_key: false,
          is_foreign_key: false,
          classification_level: 'INTERNAL',
          status: 'ACTIVE',
        },
        {
          id: 'c3',
          table_id: 'tbl-collate',
          name: 'description',
          ordinal_position: 3,
          data_type: 'text',
          raw_data_type: 'text',
          collation: null,
          is_nullable: true,
          is_primary_key: false,
          is_foreign_key: false,
          classification_level: 'INTERNAL',
          status: 'ACTIVE',
        },
      ],
      tags: [],
    } as any);

    component.selectedTableId.set('tbl-collate');
    component.activeInspectorTab.set('columns');
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.isTextColumn({ data_type: 'varchar(100)' } as any)).toBe(true);
    expect(component.isTextColumn({ data_type: 'int4' } as any)).toBe(false);

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Collation');
    expect(compiled.textContent).toContain('fa_IR.utf8');
    expect(compiled.textContent).toContain('پیش‌فرض دیتابیس');
  });

  it('should support sort selector defaulting to row_count_desc and trigger search on change', async () => {
    expect(component.selectedSortBy()).toBe('row_count_desc');
    const searchSpy = vi.spyOn(store, 'search');

    component.onSortChange('row_count_asc');
    expect(component.selectedSortBy()).toBe('row_count_asc');
    expect(searchSpy).toHaveBeenCalledWith(
      '',
      true,
      expect.objectContaining({ sort_by: 'row_count_asc' })
    );

    component.onSortChange('name_asc');
    expect(component.selectedSortBy()).toBe('name_asc');
    expect(searchSpy).toHaveBeenCalledWith(
      '',
      true,
      expect.objectContaining({ sort_by: 'name_asc' })
    );
  });

  it('should toggle hideLowRowTables and pass min_rows: 10 to search', async () => {
    expect(component.hideLowRowTables()).toBe(false);
    const searchSpy = vi.spyOn(store, 'search');

    component.toggleHideLowRowTables();
    expect(component.hideLowRowTables()).toBe(true);
    expect(searchSpy).toHaveBeenCalledWith(
      '',
      true,
      expect.objectContaining({ min_rows: 10 })
    );

    component.toggleHideLowRowTables();
    expect(component.hideLowRowTables()).toBe(false);
    expect(searchSpy).toHaveBeenCalledWith(
      '',
      true,
      expect.objectContaining({ min_rows: undefined })
    );
  });

  it('should reset sort and low-row filter on resetFilters()', async () => {
    component.selectedSortBy.set('row_count_asc');
    component.hideLowRowTables.set(true);

    component.resetFilters();
    expect(component.selectedSortBy()).toBe('row_count_desc');
    expect(component.hideLowRowTables()).toBe(false);
  });

  it('should identify empty (0) and low-row (<10) tables correctly with isTableEmpty and isTableLowRow', () => {
    const emptyHit: SearchHit = {
      id: 't-empty',
      entity_type: 'TABLE',
      name: 't_empty',
      database_name: 'db',
      schema_name: 's',
      status: 'ACTIVE',
      row_count_estimate: 0,
    };
    const lowHit: SearchHit = {
      id: 't-low',
      entity_type: 'TABLE',
      name: 't_low',
      database_name: 'db',
      schema_name: 's',
      status: 'ACTIVE',
      row_count_estimate: 7,
    };
    const normalHit: SearchHit = {
      id: 't-norm',
      entity_type: 'TABLE',
      name: 't_norm',
      database_name: 'db',
      schema_name: 's',
      status: 'ACTIVE',
      row_count_estimate: 1500,
    };

    expect(component.isTableEmpty(emptyHit)).toBe(true);
    expect(component.isTableLowRow(emptyHit)).toBe(false);

    expect(component.isTableEmpty(lowHit)).toBe(false);
    expect(component.isTableLowRow(lowHit)).toBe(true);

    expect(component.isTableEmpty(normalHit)).toBe(false);
    expect(component.isTableLowRow(normalHit)).toBe(false);
  });

  it('should display foreign key target table and column in Columns and Indexes tabs', async () => {
    component.selectedTable.set({
      id: 'tbl-fk',
      schema_id: 'sch-1',
      schema_name: 'public',
      database_name: 'app_db',
      name: 'orders',
      table_type: 'TABLE',
      row_count_estimate: 50,
      size_bytes: 2048,
      status: 'ACTIVE',
      columns: [
        {
          id: 'c-fk',
          table_id: 'tbl-fk',
          name: 'user_id',
          ordinal_position: 1,
          data_type: 'uuid',
          raw_data_type: 'uuid',
          is_nullable: false,
          is_primary_key: false,
          is_foreign_key: true,
          referenced_table: 'users',
          referenced_column: 'id',
          classification_level: 'INTERNAL',
          status: 'ACTIVE',
        },
      ],
      tags: [],
    } as any);

    component.selectedTableId.set('tbl-fk');
    component.activeInspectorTab.set('columns');
    fixture.detectChanges();
    await fixture.whenStable();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('users.id');

    component.catalogObjects.set({
      counts: { tables: 0, views: 0, indexes: 1, procedures: 0, functions: 0 },
      tables: [],
      views: [],
      indexes: [
        {
          id: 'idx-fk',
          name: 'idx_orders_user_id',
          object_type: 'INDEX',
          database_name: 'app_db',
          schema_name: 'public',
          table_name: 'orders',
          details: {
            table_id: 'tbl-fk',
            index_type: 'BTREE',
            is_unique: false,
            is_primary: false,
            columns_list: ['user_id'],
            foreign_key_targets: [
              { column: 'user_id', referenced_table: 'users', referenced_column: 'id' },
            ],
            definition: 'CREATE INDEX idx_orders_user_id ON orders (user_id)',
          },
        } as any,
      ],
      procedures: [],
      functions: [],
    });
    component.activeInspectorTab.set('indexes');
    fixture.detectChanges();
    await fixture.whenStable();

    expect(compiled.textContent).toContain('users.id');
  });

  it('should expand and collapse physical database tree nodes individually and via expand/collapse all', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    component.isTreeLoading.set(false);

    component.physicalTree.set([
      {
        id: 'conn-1',
        name: 'prod_pg',
        database_name: 'prod',
        engine_type: 'POSTGRESQL',
        schemas: [
          { id: 'sch-1', name: 'public', table_count: 2, tables: [] },
        ],
      },
      {
        id: 'conn-2',
        name: 'empty_db',
        database_name: 'empty',
        engine_type: 'MYSQL',
        schemas: [],
      },
    ]);
    component.activeTreeTab.set('physical');
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const treePane = compiled.querySelector('.tree-pane') as HTMLElement;
    expect(treePane.textContent).toContain('prod_pg');
    expect(treePane.textContent).not.toContain('public');

    // Toggle conn-1 via its chevron button without triggering filterByDatabase
    const filterSpy = vi.spyOn(component, 'filterByDatabase');
    const toggleBtn = treePane.querySelector('.node-db .tree-toggle-btn') as HTMLButtonElement;
    expect(toggleBtn).toBeTruthy();
    toggleBtn.click();
    fixture.detectChanges();

    expect(component.isPhysicalNodeExpanded('conn-1')).toBe(true);
    expect(treePane.textContent).toContain('public');
    expect(filterSpy).not.toHaveBeenCalled();

    // Collapse conn-1 again
    toggleBtn.click();
    fixture.detectChanges();
    expect(component.isPhysicalNodeExpanded('conn-1')).toBe(false);
    expect(treePane.textContent).not.toContain('public');

    // Expand all and collapse all
    component.expandAllTreeNodes();
    fixture.detectChanges();
    expect(component.isPhysicalNodeExpanded('conn-1')).toBe(true);
    expect(component.isPhysicalNodeExpanded('conn-2')).toBe(false);
    expect(treePane.textContent).toContain('public');

    component.collapseAllTreeNodes();
    fixture.detectChanges();
    expect(component.isPhysicalNodeExpanded('conn-1')).toBe(false);
    expect(treePane.textContent).not.toContain('public');
  });

  it('should expand and collapse category tree nodes recursively', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    component.isTreeLoading.set(false);

    component.categoryTree.set([
      {
        id: 'cat-1',
        name: 'فروش',
        tables: [{ id: 'tbl-1', name: 'orders' }],
        children: [
          {
            id: 'cat-2',
            name: 'فاکتورها',
            tables: [{ id: 'tbl-2', name: 'invoices' }],
            children: [],
          },
        ],
      },
    ]);
    component.activeTreeTab.set('category');
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const treePane = compiled.querySelector('.tree-pane') as HTMLElement;
    expect(treePane.textContent).toContain('فروش');
    expect(treePane.textContent).not.toContain('orders');
    expect(treePane.textContent).not.toContain('فاکتورها');

    // Expand root category
    const filterSpy = vi.spyOn(component, 'filterByCategory');
    const rootToggle = treePane.querySelector('.node-cat .tree-toggle-btn') as HTMLButtonElement;
    expect(rootToggle).toBeTruthy();
    rootToggle.click();
    fixture.detectChanges();

    expect(treePane.textContent).toContain('orders');
    expect(treePane.textContent).toContain('فاکتورها');
    expect(treePane.textContent).not.toContain('invoices');
    expect(filterSpy).not.toHaveBeenCalled();

    // Expand all categories recursively
    component.expandAllTreeNodes();
    fixture.detectChanges();
    expect(treePane.textContent).toContain('invoices');

    // Collapse all categories
    component.collapseAllTreeNodes();
    fixture.detectChanges();
    expect(treePane.textContent).not.toContain('orders');
    expect(treePane.textContent).not.toContain('فاکتورها');
    expect(treePane.textContent).not.toContain('invoices');
  });
});

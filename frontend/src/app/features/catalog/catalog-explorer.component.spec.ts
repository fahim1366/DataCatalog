import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
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
    expect(component.kpiDynamicIcon()).toBe('📊');
    expect(component.kpiDynamicCount()).toBe(3); // from searchHits

    // Views
    component.setActiveObjectTab('views');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل نماها');
    expect(component.kpiDynamicIcon()).toBe('👁️');
    expect(component.kpiDynamicCount()).toBe(2);

    // Indexes
    component.setActiveObjectTab('indexes');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل ایندکس‌ها');
    expect(component.kpiDynamicIcon()).toBe('⚡');
    expect(component.kpiDynamicCount()).toBe(3);

    // Procedures
    component.setActiveObjectTab('procedures');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل روال‌های ذخیره');
    expect(component.kpiDynamicIcon()).toBe('⚙️');
    expect(component.kpiDynamicCount()).toBe(1);

    // Functions
    component.setActiveObjectTab('functions');
    expect(component.kpiDynamicLabel()).toBe('تعداد کل توابع');
    expect(component.kpiDynamicIcon()).toBe('ƒ');
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
});

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
});

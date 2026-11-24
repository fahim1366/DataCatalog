import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { ScheduleManagementComponent } from './schedule-management.component';
import { CategoryTreeNode } from '../catalog/models/catalog.models';
import { DatabaseConnection } from '../connections/models/connection.models';
import { SyncScheduleData } from './schedule-management.component';

describe('ScheduleManagementComponent', () => {
  let fixture: ComponentFixture<ScheduleManagementComponent>;
  let component: ScheduleManagementComponent;
  let httpMock: HttpTestingController;

  const mockCategories: CategoryTreeNode[] = [
    {
      id: 'cat-sales',
      name: 'فروش و بازاریابی',
      children: [
        {
          id: 'cat-sales-crm',
          name: 'سیستم CRM',
          children: [],
          tables: [],
          databases: [
            {
              id: 'conn-crm',
              name: 'CRM Database',
              engine_type: 'POSTGRESQL',
              database_name: 'crm_db',
            },
          ],
        },
      ],
      tables: [],
      databases: [],
    },
  ];

  const mockConnections: DatabaseConnection[] = [
    {
      id: 'conn-crm',
      name: 'CRM Database',
      engine_type: 'POSTGRESQL',
      database_name: 'crm_db',
      host: '10.0.0.5',
      port: 5432,
      owner: 'sales_team',
      is_active: true,
      category_id: 'cat-sales-crm',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'conn-uncat',
      name: 'Standalone Analytics DB',
      engine_type: 'CLICKHOUSE',
      database_name: 'analytics_raw',
      host: '10.0.0.99',
      port: 8123,
      owner: 'bi_team',
      is_active: true,
      category_id: null as any,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ];

  const mockSchedule: SyncScheduleData = {
    id: 'sched-1',
    connection_id: 'conn-crm',
    cron_expression: '0 2 * * *',
    is_enabled: true,
    last_run_at: '2026-11-24T02:00:00Z',
    last_run_status: 'SUCCESS',
    last_run_message: 'همگام‌سازی با موفقیت انجام شد.',
    next_run_at: '2026-11-25T02:00:00Z',
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-24T02:00:00Z',
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ScheduleManagementComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(ScheduleManagementComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    httpMock.verify();
  });

  function setupInitialData() {
    fixture.detectChanges();

    const reqCat = httpMock.expectOne('/api/v1/categories/tree');
    expect(reqCat.request.method).toBe('GET');
    reqCat.flush(mockCategories);

    const reqConn = httpMock.expectOne('/api/v1/connections');
    expect(reqConn.request.method).toBe('GET');
    reqConn.flush(mockConnections);

    // Initial connection auto-selected: conn-crm
    const reqSched = httpMock.expectOne('/api/v1/connections/conn-crm/schedule');
    expect(reqSched.request.method).toBe('GET');
    reqSched.flush(mockSchedule);

    fixture.detectChanges();
  }

  it('should initialize and load categories and connections into hierarchical tree with uncategorized branch', () => {
    setupInitialData();

    expect(component.rawCategories().length).toBe(1);
    expect(component.connections().length).toBe(2);

    const displayTree = component.displayTree();
    expect(displayTree.length).toBe(1);
    expect(displayTree[0].name).toBe('فروش و بازاریابی');

    const uncatNode = component.uncategorizedNode();
    expect(uncatNode).toBeTruthy();
    expect(uncatNode!.name).toBe('پایگاه‌های داده دسته‌بندی‌نشده');
    expect(uncatNode!.connections.length).toBe(1);
    expect(uncatNode!.connections[0].name).toBe('Standalone Analytics DB');

    expect(component.totalFilteredDatabasesCount()).toBe(2);
    expect(component.selectedDatabase()?.id).toBe('conn-crm');
  });

  it('should filter tree by multi-criteria search and clear filters', () => {
    setupInitialData();

    // Filter by engine CLICKHOUSE
    component.filterEngineType.set('CLICKHOUSE');
    fixture.detectChanges();

    expect(component.hasActiveTreeFilters()).toBe(true);
    expect(component.displayTree().length).toBe(0); // CRM db is POSTGRESQL
    expect(component.uncategorizedNode()?.connections.length).toBe(1);
    expect(component.totalFilteredDatabasesCount()).toBe(1);

    // Clear filters
    component.clearTreeFilters();
    fixture.detectChanges();

    expect(component.hasActiveTreeFilters()).toBe(false);
    expect(component.displayTree().length).toBe(1);
    expect(component.totalFilteredDatabasesCount()).toBe(2);
  });

  it('should update cron expression when preset chip is clicked', () => {
    setupInitialData();

    const preset = component.cronPresets[0]; // '0 */6 * * *'
    component.applyPreset(preset);

    expect(component.scheduleForm.controls.cron_expression.value).toBe('0 */6 * * *');
    expect(component.cronHumanPreview()).toContain('هر ۶ ساعت');
  });

  it('should save schedule when form is submitted', () => {
    setupInitialData();

    component.scheduleForm.patchValue({
      cron_expression: '0 3 * * *',
      is_enabled: true,
    });

    component.saveSchedule();

    const req = httpMock.expectOne('/api/v1/connections/conn-crm/schedule');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({
      cron_expression: '0 3 * * *',
      is_enabled: true,
    });

    req.flush({
      ...mockSchedule,
      cron_expression: '0 3 * * *',
    });

    expect(component.toastMessage()).toContain('با موفقیت ذخیره شد');
  });

  it('should trigger manual sync on triggerSyncNow', () => {
    setupInitialData();

    component.triggerSyncNow();

    const req = httpMock.expectOne('/api/v1/connections/conn-crm/sync');
    expect(req.request.method).toBe('POST');
    req.flush(mockSchedule);

    expect(component.toastMessage()).toContain('با موفقیت انجام شد');
  });
});

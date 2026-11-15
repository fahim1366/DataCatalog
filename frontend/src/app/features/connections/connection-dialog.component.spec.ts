import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { ConnectionDialogComponent } from './connection-dialog.component';
import { ConnectionService } from './services/connection.service';
import { CategoryTreeNode } from '../catalog/models/catalog.models';

const mockCategoryTree: CategoryTreeNode[] = [
  {
    id: 'parent-cat-1',
    name: 'شهرداری و شهرسازی (Parent Branch)',
    tables: [],
    databases: [],
    children: [
      {
        id: 'leaf-cat-1',
        name: 'پروانه‌های ساختمانی (Leaf 1)',
        tables: [],
        databases: [],
        children: [],
      },
      {
        id: 'leaf-cat-2',
        name: 'طرح تفصیلی (Leaf 2)',
        tables: [],
        databases: [],
        children: [],
      },
    ],
  },
  {
    id: 'leaf-cat-root',
    name: 'امور مالیاتی مستقل (Root Leaf)',
    tables: [],
    databases: [],
    children: [],
  },
];

describe('ConnectionDialogComponent', () => {
  let fixture: ComponentFixture<ConnectionDialogComponent>;
  let component: ConnectionDialogComponent;
  let connectionService: ConnectionService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConnectionDialogComponent],
      providers: [
        ConnectionService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    connectionService = TestBed.inject(ConnectionService);
    vi.spyOn(connectionService, 'getCategoryTree').mockReturnValue(of(mockCategoryTree));

    fixture = TestBed.createComponent(ConnectionDialogComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should initialize with default values and POSTGRESQL port 5432', () => {
    expect(component.form.controls.engine_type.value).toBe('POSTGRESQL');
    expect(component.form.controls.port.value).toBe(5432);
    expect(component.form.controls.host.value).toBe('localhost');
  });

  it('should update port automatically when engine dialect changes', () => {
    component.form.controls.engine_type.setValue('CLICKHOUSE');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(8123);

    component.form.controls.engine_type.setValue('MSSQL');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(1433);

    component.form.controls.engine_type.setValue('ORACLE');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(1521);

    component.form.controls.engine_type.setValue('MYSQL');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(3306);
  });

  it('should only allow selecting leaf nodes in category tree and expand parent branches', () => {
    // Attempt clicking parent node: it should expand/collapse but NOT select
    const parentItem = component.flattenedCategoryList().find(item => item.node.id === 'parent-cat-1');
    expect(parentItem).toBeTruthy();
    expect(parentItem!.isLeaf).toBe(false);

    component.onNodeClick(parentItem!);
    fixture.detectChanges();

    // category_id should NOT be set to parent
    expect(component.form.controls.category_id.value).not.toBe('parent-cat-1');

    // Click root leaf node: should select leaf
    const rootLeaf = component.flattenedCategoryList().find(item => item.node.id === 'leaf-cat-root');
    expect(rootLeaf).toBeTruthy();
    expect(rootLeaf!.isLeaf).toBe(true);

    component.onNodeClick(rootLeaf!);
    fixture.detectChanges();

    expect(component.form.controls.category_id.value).toBe('leaf-cat-root');
    expect(component.selectedCategoryName()).toBe('امور مالیاتی مستقل (Root Leaf)');

    // Toggle parent again to expand it and select leaf-cat-1
    component.toggleExpanded('parent-cat-1');
    fixture.detectChanges();
    const leaf1 = component.flattenedCategoryList().find(item => item.node.id === 'leaf-cat-1');
    expect(leaf1).toBeTruthy();
    expect(leaf1!.isLeaf).toBe(true);
    component.onNodeClick(leaf1!);
    fixture.detectChanges();
    expect(component.form.controls.category_id.value).toBe('leaf-cat-1');
  });

  it('should emit save event with connection payload including category_id when form is submitted validly', () => {
    let emittedPayload: any = null;
    component.save.subscribe((val) => {
      emittedPayload = val;
    });

    component.form.patchValue({
      name: 'Analytics DW',
      category_id: 'leaf-cat-1',
      engine_type: 'POSTGRESQL',
      host: 'dw.internal.company.com',
      port: 5432,
      database_name: 'analytics_dw',
      default_schema: 'mart',
      owner: 'bi-team',
      username: 'bi_service_account',
      password: 'SuperSecretPassword123!',
      cron_expression: '0 4 * * *',
    });

    component.onSubmit();

    expect(emittedPayload).toBeTruthy();
    expect(emittedPayload.name).toBe('Analytics DW');
    expect(emittedPayload.category_id).toBe('leaf-cat-1');
    expect(emittedPayload.engine_type).toBe('POSTGRESQL');
    expect(emittedPayload.host).toBe('dw.internal.company.com');
    expect(emittedPayload.port).toBe(5432);
    expect(emittedPayload.database_name).toBe('analytics_dw');
    expect(emittedPayload.default_schema).toBe('mart');
    expect(emittedPayload.owner).toBe('bi-team');
    expect(emittedPayload.credentials.username).toBe('bi_service_account');
    expect(emittedPayload.credentials.password).toBe('SuperSecretPassword123!');
    expect(emittedPayload.cron_expression).toBe('0 4 * * *');
  });

  it('should not emit save event when form is invalid', () => {
    let wasEmitted = false;
    component.save.subscribe(() => {
      wasEmitted = true;
    });

    component.form.controls.name.setValue('');
    component.onSubmit();

    expect(wasEmitted).toBe(false);
  });

  it('should emit cancel event when backdrop is clicked', () => {
    let cancelled = false;
    component.cancel.subscribe(() => {
      cancelled = true;
    });

    const backdrop = fixture.debugElement.query(By.css('.modal-backdrop'));
    backdrop.nativeElement.dispatchEvent(new MouseEvent('click'));

    expect(cancelled).toBe(true);
  });
});

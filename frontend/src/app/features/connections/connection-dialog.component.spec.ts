import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of, throwError } from 'rxjs';
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
    expect(component.engineHint()).toContain('۸۱۲۳');

    component.form.controls.engine_type.setValue('MSSQL');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(1433);
    expect(component.engineHint()).toContain('۱۴۳۳');

    component.form.controls.engine_type.setValue('ORACLE');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(1521);
    expect(component.engineHint()).toContain('۱۵۲۱');

    component.form.controls.engine_type.setValue('MYSQL');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(3306);
    expect(component.engineHint()).toContain('۳۳۰۶');

    component.form.controls.engine_type.setValue('MONGODB');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(27017);
    expect(component.engineHint()).toContain('۲۷۰۱۷');

    component.form.controls.engine_type.setValue('POSTGRESQL');
    component.onEngineChange();
    expect(component.form.controls.port.value).toBe(5432);
    expect(component.engineHint()).toContain('۵۴۳۲');
  });

  it('should apply quick parameters template to custom_params_json', () => {
    const template = '{ "service_name": "ORCLPDB1" }';
    component.applyQuickParam(template);
    expect(component.form.controls.custom_params_json.value).toBe(template);
    expect(component.form.controls.custom_params_json.dirty).toBe(true);
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

  it('should validate JSON syntax on custom_params_json and emit parsed object on submit', () => {
    let emittedPayload: any = null;
    component.save.subscribe((val) => {
      emittedPayload = val;
    });

    component.form.patchValue({
      name: 'Custom DW',
      engine_type: 'POSTGRESQL',
      host: 'dw.internal.company.com',
      port: 5432,
      database_name: 'analytics_dw',
      username: 'bi_admin',
      password: 'Pass123!',
      custom_params_json: '{ "sslmode": "require", "connect_timeout": 15 }',
    });

    expect(component.form.controls.custom_params_json.valid).toBe(true);

    component.onSubmit();
    expect(emittedPayload).toBeTruthy();
    expect(emittedPayload.custom_params).toEqual({
      sslmode: 'require',
      connect_timeout: 15,
    });
  });

  it('should reject invalid JSON syntax in custom_params_json and prevent submit', () => {
    let wasEmitted = false;
    component.save.subscribe(() => {
      wasEmitted = true;
    });

    component.form.patchValue({
      name: 'Custom DW',
      engine_type: 'POSTGRESQL',
      host: 'dw.internal.company.com',
      port: 5432,
      database_name: 'analytics_dw',
      username: 'bi_admin',
      password: 'Pass123!',
      custom_params_json: '{ sslmode: bad_syntax }',
    });

    expect(component.form.controls.custom_params_json.invalid).toBe(true);
    expect(component.form.controls.custom_params_json.errors?.['invalidJson']).toBeTruthy();

    component.onSubmit();
    expect(wasEmitted).toBe(false);
    expect(component.isCustomParamsOpen()).toBe(true);
  });

  it('should format Persian numbers correctly for discovery badge', () => {
    expect(component.toPersianNumber(3)).toBe('۳');
    expect(component.toPersianNumber(10)).toBe('۱۰');
  });

  it('should discover databases, populate combobox, show success badge, and open dropdown', () => {
    vi.spyOn(connectionService, 'discoverDatabases').mockReturnValue(
      of({
        success: true,
        databases: ['postgres', 'analytics_dw', 'orders_db'],
        latency_ms: 18,
        message: 'Discovered 3 databases',
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'secret_password',
      database_name: '',
    });

    component.discoverDatabases();
    fixture.detectChanges();

    expect(component.discoveredDatabases()).toEqual(['postgres', 'analytics_dw', 'orders_db']);
    expect(component.discoveryStatus()).toBe('success');
    expect(component.discoveryMessage()).toBe('✓ ۳ پایگاه‌داده شناسایی شد');
    expect(component.isDropdownOpen()).toBe(true);
    expect(component.form.controls.database_name.value).toBe('postgres');
  });

  it('should allow selecting a discovered database from the dropdown into form.controls.database_name', () => {
    vi.spyOn(connectionService, 'discoverDatabases').mockReturnValue(
      of({
        success: true,
        databases: ['postgres', 'analytics_dw', 'orders_db'],
        latency_ms: 15,
        message: 'Discovered 3 databases',
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'secret_password',
      database_name: '',
    });

    component.discoverDatabases();
    fixture.detectChanges();

    expect(component.isDropdownOpen()).toBe(true);

    // Select 'orders_db' from discovered options
    component.selectDiscoveredDatabase('orders_db');
    fixture.detectChanges();

    expect(component.form.controls.database_name.value).toBe('orders_db');
    expect(component.isDropdownOpen()).toBe(false);
  });

  it('should auto-trigger discoverDatabases when host, port, username, and password are valid', () => {
    vi.useFakeTimers();
    const discoverSpy = vi.spyOn(connectionService, 'discoverDatabases').mockReturnValue(
      of({
        success: true,
        databases: ['postgres', 'analytics_dw'],
        latency_ms: 10,
        message: 'Discovered',
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'secret_password',
    });

    // Advance by debounce time 600ms
    vi.advanceTimersByTime(650);

    expect(discoverSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        engine_type: 'POSTGRESQL',
        host: 'pg.internal.com',
        port: 5432,
        credentials: {
          username: 'db_admin',
          password: 'secret_password',
        },
      })
    );
    expect(component.discoveredDatabases()).toEqual(['postgres', 'analytics_dw']);
    expect(component.isDropdownOpen()).toBe(true);

    vi.useRealTimers();
  });

  it('should display Persian error message with hint when discoverDatabases fails due to auth failure', () => {
    vi.spyOn(connectionService, 'discoverDatabases').mockReturnValue(
      of({
        success: false,
        databases: [],
        latency_ms: 0,
        message: 'Authentication failed for user db_admin',
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'wrong_password',
    });

    component.discoverDatabases();
    fixture.detectChanges();

    expect(component.discoveryStatus()).toBe('error');
    expect(component.discoveryMessage()).toBe(
      'خطا در احراز هویت: نام کاربری یا رمز عبور نامعتبر است. لطفاً مشخصات کاربری را بررسی کرده یا از دکمه «تست اتصال» در پایین فرم استفاده کنید.'
    );
    expect(component.discoveredDatabases().length).toBe(0);
  });

  it('should display Persian error message with hint when discoverDatabases encounters a network error', () => {
    vi.spyOn(connectionService, 'discoverDatabases').mockReturnValue(
      throwError(() => ({ error: { detail: 'Network error or host unreachable' } }))
    );

    component.form.patchValue({
      host: 'pg.unreachable.com',
      port: 5432,
      username: 'db_admin',
      password: 'secret_password',
    });

    component.discoverDatabases();
    fixture.detectChanges();

    expect(component.discoveryStatus()).toBe('error');
    expect(component.discoveryMessage()).toBe(
      'آدرس سرور (هاست) یافت نشد یا شبکه در دسترس نیست. لطفاً آدرس سرور و اتصال شبکه را بررسی نموده یا از دکمه «تست اتصال» استفاده کنید.'
    );
  });

  it('should allow manual typing into database_name without forcing selection', () => {
    component.form.controls.database_name.setValue('manual_custom_db');
    expect(component.form.controls.database_name.value).toBe('manual_custom_db');
  });

  it('should run live diagnostic pre-save test, translate success message to Persian in banner, and display inline result banner', () => {
    vi.spyOn(connectionService, 'testUnsavedConnection').mockReturnValue(
      of({
        success: true,
        latency_ms: 24,
        dialect: 'POSTGRESQL',
        message: 'Successfully connected and verified PostgreSQL 16.2',
        tested_at: new Date().toISOString(),
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'secret_password',
      database_name: 'analytics_dw',
    });

    component.testUnsavedConnection();
    fixture.detectChanges();

    expect(component.testResult()).toBeTruthy();
    expect(component.testResult()?.success).toBe(true);
    expect(component.testResult()?.latency_ms).toBe(24);
    expect(component.testResult()?.message).toBe('اتصال به پایگاه‌داده با موفقیت برقرار شد.');

    const bannerHeader = fixture.debugElement.query(By.css('.test-result-header strong'));
    expect(bannerHeader.nativeElement.textContent).toContain('اتصال به پایگاه‌داده با موفقیت برقرار شد.');
  });

  it('should display Persian error message with hint when test connection fails', () => {
    vi.spyOn(connectionService, 'testUnsavedConnection').mockReturnValue(
      of({
        success: false,
        latency_ms: 0,
        dialect: 'POSTGRESQL',
        message: 'password authentication failed for user "db_admin"',
        tested_at: new Date().toISOString(),
      })
    );

    component.form.patchValue({
      host: 'pg.internal.com',
      port: 5432,
      username: 'db_admin',
      password: 'wrong_password',
      database_name: 'analytics_dw',
    });

    component.testUnsavedConnection();
    fixture.detectChanges();

    expect(component.testResult()).toBeTruthy();
    expect(component.testResult()?.success).toBe(false);
    expect(component.testResult()?.message).toContain('خطا در احراز هویت');
    expect(component.testResult()?.message).toContain('تست اتصال');
  });

  it('should render clean Persian labels and RTL credentials row order', () => {
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;

    // RTL credentials order: username on the right (1st column), password on the left (2nd column)
    const credsRow = compiled.querySelector('.row-credentials');
    expect(credsRow).toBeTruthy();
    const credFormGroups = credsRow!.querySelectorAll('.form-group');
    expect(credFormGroups.length).toBe(2);

    const firstLabel = credFormGroups[0].querySelector('label')?.textContent?.trim();
    const secondLabel = credFormGroups[1].querySelector('label')?.textContent?.trim();
    expect(firstLabel).toContain('نام کاربری اتصال');
    expect(firstLabel).not.toContain('(Username)');
    expect(secondLabel).toContain('رمز عبور');
    expect(secondLabel).not.toContain('(Password)');

    // Concise and clean Persian labels
    const textContent = compiled.textContent || '';
    expect(textContent).toContain('دسته‌بندی موضوعی در کاتالوگ');
    expect(textContent).not.toContain('دسته‌بندی موضوعی در کاتالوگ (Topic / Category)');
    expect(textContent).toContain('نام دیتابیس');
    expect(textContent).not.toContain('نام دیتابیس (Database Name)');
    expect(textContent).toContain('اسکیمای پیش‌فرض');
    expect(textContent).not.toContain('اسکیمای پیش‌فرض (Default Schema)');
    expect(textContent).toContain('تست اتصال');
    expect(textContent).not.toContain('تست اتصال (Test Connection)');
    expect(textContent).toContain('ثبت پایگاه‌داده');
    expect(textContent).not.toContain('ثبت پایگاه‌داده جدید (Register Database)');
  });
});

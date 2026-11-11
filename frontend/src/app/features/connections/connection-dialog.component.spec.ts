import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ConnectionDialogComponent } from './connection-dialog.component';

describe('ConnectionDialogComponent', () => {
  let fixture: ComponentFixture<ConnectionDialogComponent>;
  let component: ConnectionDialogComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConnectionDialogComponent],
    }).compileComponents();

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

  it('should emit save event with connection payload when form is submitted validly', () => {
    let emittedPayload: any = null;
    component.save.subscribe((val) => {
      emittedPayload = val;
    });

    component.form.patchValue({
      name: 'Analytics DW',
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


import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  FormBuilder,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import {
  ConnectionCreateRequest,
  DatabaseConnection,
  DatabaseEngine,
} from './models/connection.models';
import { cronValidator } from './validators/cron.validator';

@Component({
  selector: 'app-connection-dialog',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick($event)">
      <div class="modal-card" role="dialog" aria-labelledby="dialog-title" aria-modal="true">
        <header class="modal-header">
          <h2 id="dialog-title">
            {{ connectionToEdit ? 'ویرایش اتصال پایگاه‌داده (Edit Connection)' : 'ثبت اتصال پایگاه‌داده جدید (Register Connection)' }}
          </h2>
          <button type="button" class="btn-close" (click)="cancel.emit()" aria-label="Close dialog">✕</button>
        </header>

        <form [formGroup]="form" (ngSubmit)="onSubmit()" class="connection-form">
          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="name">نام اتصال (Connection Name) <span class="required">*</span></label>
              <input
                id="name"
                type="text"
                class="form-control"
                formControlName="name"
                placeholder="مثال: انبار داده تحلیلی سازمانی (Production Analytics Warehouse)"
              />
              @if (form.controls.name.touched && form.controls.name.invalid) {
                <div class="field-error">نام اتصال الزامی است (Connection name is required).</div>
              }
            </div>
          </div>

          <div class="form-row grid-3">
            <div class="form-group">
              <label for="engine_type">گویش / موتور دیتابیس (Engine Dialect) <span class="required">*</span></label>
              <select
                id="engine_type"
                class="form-control"
                formControlName="engine_type"
                (change)="onEngineChange()"
              >
                @for (engine of supportedEngines; track engine) {
                  <option [value]="engine">{{ engine }}</option>
                }
              </select>
            </div>

            <div class="form-group col-span-2">
              <label for="host">آدرس سرور / هاست (Host / Address) <span class="required">*</span></label>
              <input
                id="host"
                type="text"
                class="form-control font-mono"
                formControlName="host"
                placeholder="hostname or IP"
              />
              @if (form.controls.host.touched && form.controls.host.invalid) {
                <div class="field-error">آدرس هاست الزامی است (Host is required).</div>
              }
            </div>
          </div>

          <div class="form-row grid-3">
            <div class="form-group">
              <label for="port">پورت اتصال (Port) <span class="required">*</span></label>
              <input
                id="port"
                type="number"
                class="form-control font-mono"
                formControlName="port"
              />
              @if (form.controls.port.touched && form.controls.port.invalid) {
                <div class="field-error">پورت معتبر (۱ تا ۶۵۵۳۵) الزامی است (Valid port (1-65535) is required).</div>
              }
            </div>

            <div class="form-group">
              <label for="database_name">نام دیتابیس (Database Name) <span class="required">*</span></label>
              <input
                id="database_name"
                type="text"
                class="form-control font-mono"
                formControlName="database_name"
                placeholder="Target database/catalog"
              />
              @if (form.controls.database_name.touched && form.controls.database_name.invalid) {
                <div class="field-error">نام پایگاه‌داده الزامی است (Database name is required).</div>
              }
            </div>

            <div class="form-group">
              <label for="default_schema">اسکیمای پیش‌فرض (Default Schema)</label>
              <input
                id="default_schema"
                type="text"
                class="form-control font-mono"
                formControlName="default_schema"
                placeholder="مثال: public یا dbo"
              />
            </div>
          </div>

          <div class="form-row grid-2">
            <div class="form-group">
              <label for="username">نام کاربری اتصال (Username) <span class="required">*</span></label>
              <input
                id="username"
                type="text"
                class="form-control font-mono"
                formControlName="username"
                autocomplete="off"
              />
              @if (form.controls.username.touched && form.controls.username.invalid) {
                <div class="field-error">نام کاربری الزامی است (Username is required).</div>
              }
            </div>

            <div class="form-group">
              <label for="password">
                {{ connectionToEdit ? 'رمز عبور جدید (اختیاری در ویرایش)' : 'رمز عبور (Password)' }}
                @if (!connectionToEdit) { <span class="required">*</span> }
              </label>
              <input
                id="password"
                type="password"
                class="form-control font-mono"
                formControlName="password"
                autocomplete="new-password"
                [placeholder]="connectionToEdit ? 'در صورت عدم تغییر، خالی بگذارید' : 'رمز عبور امن دیتابیس'"
              />
              @if (form.controls.password.touched && form.controls.password.invalid) {
                <div class="field-error">رمز عبور الزامی است (Password is required).</div>
              }
            </div>
          </div>

          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="owner">مالک / تیم فنی مسئول (Owner / Team)</label>
              <input
                id="owner"
                type="text"
                class="form-control"
                formControlName="owner"
                placeholder="مثال: data-platform یا bi-team"
              />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="cron_expression">برنامه زمان‌بندی همگام‌سازی (Sync Schedule - 5-part Cron)</label>
              <input
                id="cron_expression"
                type="text"
                class="form-control font-mono"
                formControlName="cron_expression"
                placeholder="مثال: 0 2 * * * (هر شب ساعت ۲ بامداد)"
              />
              <span class="cron-hint">الگوی استاندارد کرون: &lt;دقیقه&gt; &lt;ساعت&gt; &lt;روز ماه&gt; &lt;ماه&gt; &lt;روز هفته&gt;</span>

              @if (form.controls.cron_expression.errors?.['invalidCron']) {
                <div class="cron-error-feedback">
                  ✕ {{ form.controls.cron_expression.errors?.['invalidCron']?.message }}
                </div>
              } @else if (form.controls.cron_expression.value && form.controls.cron_expression.valid) {
                <div class="cron-valid-feedback">
                  ✓ الگوی معتبر زمان‌بندی (Valid 5-part cron schedule)
                  <div class="cron-human-summary">{{ getCronHumanSummary(form.controls.cron_expression.value) }}</div>
                </div>
              }
            </div>
          </div>

          @if (errorMessage()) {
            <div class="alert-error">{{ errorMessage() }}</div>
          }

          <footer class="modal-footer">
            <button
              type="button"
              class="btn-secondary"
              (click)="cancel.emit()"
            >
              انصراف (Cancel)
            </button>
            <button
              type="submit"
              class="btn-primary"
              [disabled]="form.invalid || isSubmitting()"
            >
              @if (isSubmitting()) {
                <span>در حال ذخیره و رمزنگاری (Encrypting & Saving...)...</span>
              } @else if (connectionToEdit) {
                <span>ذخیره تغییرات اتصال (Update Connection)</span>
              } @else {
                <span>ثبت و ذخیره اتصال (Register Connection)</span>
              }
            </button>
          </footer>
        </form>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.6);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 1rem;
      direction: rtl;
      font-family: var(--font-family-sans, 'Vazirmatn', sans-serif);
    }
    .modal-card {
      background: #ffffff;
      border-radius: 8px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
      width: 100%;
      max-width: 680px;
      max-height: 90vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1.25rem 1.5rem;
      border-bottom: 1px solid #e2e8f0;
    }
    .modal-header h2 {
      margin: 0;
      font-size: 1.15rem;
      font-weight: 700;
      color: #0f172a;
    }
    .btn-close {
      background: none;
      border: 1px solid #cbd5e1;
      border-radius: 50%;
      font-size: 1rem;
      cursor: pointer;
      color: #64748b;
      width: 30px;
      height: 30px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .btn-close:hover {
      background: #f1f5f9;
      color: #0f172a;
    }
    .connection-form {
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1rem;
    }
    .form-row.grid-3 {
      grid-template-columns: 1fr 1fr 1fr;
    }
    .form-row.grid-2 {
      grid-template-columns: 1fr 1fr;
    }
    .col-span-2 {
      grid-column: span 2;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .form-group label {
      font-size: 0.82rem;
      font-weight: 600;
      color: #334155;
    }
    .required {
      color: #ef4444;
    }
    .form-control {
      padding: 0.5rem 0.75rem;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font-size: 0.85rem;
      color: #0f172a;
      background: #ffffff;
      outline: none;
      transition: border-color 0.15s;
    }
    .form-control:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
    }
    .font-mono {
      font-family: var(--font-family-mono, monospace);
    }
    .field-error {
      color: #ef4444;
      font-size: 0.75rem;
    }
    .cron-hint {
      color: #64748b;
      font-size: 0.75rem;
    }
    .cron-error-feedback {
      color: #dc2626;
      font-size: 0.75rem;
      font-weight: 500;
      margin-top: 0.25rem;
    }
    .cron-valid-feedback {
      color: #16a34a;
      font-size: 0.75rem;
      font-weight: 500;
      margin-top: 0.25rem;
    }
    .cron-human-summary {
      color: #0f172a;
      font-size: 0.75rem;
      font-weight: 600;
      margin-top: 0.15rem;
    }
    .alert-error {
      padding: 0.75rem 1rem;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 6px;
      color: #991b1b;
      font-size: 0.85rem;
    }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 0.75rem;
      padding-top: 1rem;
      border-top: 1px solid #e2e8f0;
    }
    .btn-secondary {
      padding: 0.5rem 1rem;
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-family: inherit;
    }
    .btn-primary {
      padding: 0.5rem 1.25rem;
      background: #2563eb;
      color: #ffffff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-family: inherit;
    }
    .btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);

  @Input() connectionToEdit: DatabaseConnection | null = null;
  @Output() readonly save = new EventEmitter<ConnectionCreateRequest>();
  @Output() readonly cancel = new EventEmitter<void>();

  readonly supportedEngines: DatabaseEngine[] = [
    'POSTGRESQL',
    'MSSQL',
    'ORACLE',
    'MYSQL',
    'CLICKHOUSE',
  ];

  private readonly defaultPorts: Record<DatabaseEngine, number> = {
    POSTGRESQL: 5432,
    MSSQL: 1433,
    ORACLE: 1521,
    MYSQL: 3306,
    CLICKHOUSE: 8123,
  };

  readonly isSubmitting = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  readonly form = this.fb.group({
    name: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(200)],
    }),
    engine_type: this.fb.control<DatabaseEngine>('POSTGRESQL', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    host: this.fb.control<string>('localhost', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(255)],
    }),
    port: this.fb.control<number>(5432, {
      nonNullable: true,
      validators: [Validators.required, Validators.min(1), Validators.max(65535)],
    }),
    database_name: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(255)],
    }),
    default_schema: this.fb.control<string | null>('public'),
    owner: this.fb.control<string>('admin', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(255)],
    }),
    username: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    password: this.fb.control<string>('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    cron_expression: this.fb.control<string | null>('', {
      validators: [cronValidator()],
    }),
  });

  ngOnInit(): void {
    if (this.connectionToEdit) {
      const conn = this.connectionToEdit;
      this.form.patchValue({
        name: conn.name,
        engine_type: conn.engine_type,
        host: conn.host,
        port: conn.port,
        database_name: conn.database_name,
        default_schema: conn.default_schema || '',
        owner: conn.owner,
        username: conn.masked_credentials?.['username'] || 'admin',
        password: '',
        cron_expression: conn.sync_schedule?.cron_expression || '',
      });
      // In edit mode, password is optional
      this.form.controls.password.clearValidators();
      this.form.controls.password.updateValueAndValidity();
    }
  }

  onEngineChange(): void {
    const engine = this.form.controls.engine_type.value;
    if (engine && this.defaultPorts[engine]) {
      this.form.controls.port.setValue(this.defaultPorts[engine]);
    }
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.cancel.emit();
    }
  }

  getCronHumanSummary(cron: string | null): string {
    if (!cron) return '';
    const trimmed = cron.trim();
    if (trimmed === '0 2 * * *') return 'هر شب ساعت ۰۲:۰۰ بامداد (Daily at 02:00 AM)';
    if (trimmed === '0 0 * * *') return 'هر روز ساعت ۱۲:۰۰ شب (Daily at midnight)';
    if (trimmed === '0 4 * * *') return 'هر شب ساعت ۰۴:۰۰ بامداد (Daily at 04:00 AM)';
    if (trimmed === '0 * * * *') return 'هر یک ساعت یک‌بار (Every hour)';
    if (trimmed === '*/30 * * * *') return 'هر ۳۰ دقیقه یک‌بار (Every 30 minutes)';
    if (trimmed === '*/15 * * * *') return 'هر ۱۵ دقیقه یک‌بار (Every 15 minutes)';
    const parts = trimmed.split(/\s+/);
    if (parts.length === 5) {
      const [m, h] = parts;
      if (!isNaN(Number(m)) && !isNaN(Number(h))) {
        return `هر روز ساعت ${h.padStart(2, '0')}:${m.padStart(2, '0')} (Daily at ${h}:${m})`;
      }
    }
    return 'برنامه همگام‌سازی زمان‌بندی شده (Custom Scheduled Sync)';
  }

  onSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const val = this.form.getRawValue();
    const creds: Record<string, unknown> = {
      username: val.username,
    };
    if (val.password && val.password.trim()) {
      creds['password'] = val.password;
    }

    const request: ConnectionCreateRequest = {
      name: val.name,
      engine_type: val.engine_type,
      host: val.host,
      port: val.port,
      database_name: val.database_name,
      default_schema: val.default_schema?.trim() || undefined,
      owner: val.owner,
      credentials: creds,
      custom_params: {},
      cron_expression: val.cron_expression?.trim() || undefined,
    };

    this.save.emit(request);
  }
}

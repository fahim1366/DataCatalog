import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
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
  DatabaseEngine,
} from './models/connection.models';
import { cronValidator } from './validators/cron.validator';

interface ConnectionForm {
  name: FormControl<string>;
  engine_type: FormControl<DatabaseEngine>;
  host: FormControl<string>;
  port: FormControl<number>;
  database_name: FormControl<string>;
  default_schema: FormControl<string | null>;
  owner: FormControl<string>;
  username: FormControl<string>;
  password: FormControl<string>;
  cron_expression: FormControl<string | null>;
}

@Component({
  selector: 'app-connection-dialog',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdropClick($event)">
      <div class="modal-card" role="dialog" aria-labelledby="dialog-title" aria-modal="true">
        <header class="modal-header">
          <h2 id="dialog-title">Register Database Connection</h2>
          <button type="button" class="btn-close" (click)="cancel.emit()" aria-label="Close dialog">✕</button>
        </header>

        <form [formGroup]="form" (ngSubmit)="onSubmit()" class="connection-form">
          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="name">Connection Name <span class="required">*</span></label>
              <input
                id="name"
                type="text"
                class="form-control"
                formControlName="name"
                placeholder="e.g. Production Analytics Warehouse"
              />
              @if (form.controls.name.touched && form.controls.name.invalid) {
                <div class="field-error">Connection name is required.</div>
              }
            </div>
          </div>

          <div class="form-row grid-3">
            <div class="form-group">
              <label for="engine_type">Engine Dialect <span class="required">*</span></label>
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
              <label for="host">Host / Address <span class="required">*</span></label>
              <input
                id="host"
                type="text"
                class="form-control"
                formControlName="host"
                placeholder="hostname or IP"
              />
              @if (form.controls.host.touched && form.controls.host.invalid) {
                <div class="field-error">Host is required.</div>
              }
            </div>
          </div>

          <div class="form-row grid-3">
            <div class="form-group">
              <label for="port">Port <span class="required">*</span></label>
              <input
                id="port"
                type="number"
                class="form-control"
                formControlName="port"
              />
              @if (form.controls.port.touched && form.controls.port.invalid) {
                <div class="field-error">Valid port (1-65535) is required.</div>
              }
            </div>

            <div class="form-group">
              <label for="database_name">Database Name <span class="required">*</span></label>
              <input
                id="database_name"
                type="text"
                class="form-control"
                formControlName="database_name"
                placeholder="Target database/catalog"
              />
              @if (form.controls.database_name.touched && form.controls.database_name.invalid) {
                <div class="field-error">Database name is required.</div>
              }
            </div>

            <div class="form-group">
              <label for="default_schema">Default Schema</label>
              <input
                id="default_schema"
                type="text"
                class="form-control"
                formControlName="default_schema"
                placeholder="e.g. public or dbo"
              />
            </div>
          </div>

          <div class="form-row grid-2">
            <div class="form-group">
              <label for="username">Username <span class="required">*</span></label>
              <input
                id="username"
                type="text"
                class="form-control"
                formControlName="username"
                autocomplete="off"
              />
              @if (form.controls.username.touched && form.controls.username.invalid) {
                <div class="field-error">Username is required.</div>
              }
            </div>

            <div class="form-group">
              <label for="password">Password <span class="required">*</span></label>
              <input
                id="password"
                type="password"
                class="form-control"
                formControlName="password"
                autocomplete="new-password"
              />
              @if (form.controls.password.touched && form.controls.password.invalid) {
                <div class="field-error">Password is required.</div>
              }
            </div>
          </div>

          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="owner">Owner / Team</label>
              <input
                id="owner"
                type="text"
                class="form-control"
                formControlName="owner"
                placeholder="e.g. data-platform"
              />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group col-span-2">
              <label for="cron_expression">Sync Schedule (5-part Cron)</label>
              <input
                id="cron_expression"
                type="text"
                class="form-control font-mono"
                formControlName="cron_expression"
                placeholder="e.g. 0 2 * * * (daily at 2am)"
              />
              <span class="cron-hint">Format: &lt;min&gt; &lt;hour&gt; &lt;day-of-month&gt; &lt;month&gt; &lt;day-of-week&gt;</span>

              @if (form.controls.cron_expression.errors?.['invalidCron']) {
                <div class="cron-error-feedback">
                  ✕ {{ form.controls.cron_expression.errors?.['invalidCron']?.message }}
                </div>
              } @else if (form.controls.cron_expression.value && form.controls.cron_expression.valid) {
                <div class="cron-valid-feedback">
                  ✓ Valid 5-part cron schedule
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
              Cancel
            </button>
            <button
              type="submit"
              class="btn-primary"
              [disabled]="form.invalid || isSubmitting()"
            >
              @if (isSubmitting()) {
                <span>Encrypting & Saving...</span>
              } @else {
                <span>Register Connection</span>
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
      font-size: 1.25rem;
      font-weight: 600;
      color: #0f172a;
    }
    .btn-close {
      background: transparent;
      border: none;
      font-size: 1.25rem;
      cursor: pointer;
      color: #64748b;
    }
    .connection-form {
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .form-row {
      display: grid;
      gap: 1rem;
    }
    .grid-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .grid-3 {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .col-span-2 {
      grid-column: span 2 / span 2;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    label {
      font-size: 0.85rem;
      font-weight: 500;
      color: #334155;
    }
    .required {
      color: #dc2626;
    }
    .form-control {
      padding: 0.5rem 0.75rem;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font-size: 0.875rem;
      color: #1e293b;
      transition: border-color 0.15s ease;
    }
    .form-control:focus {
      outline: none;
      border-color: #2563eb;
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
    }
    .font-mono {
      font-family: ui-monospace, monospace;
    }
    .cron-hint {
      font-size: 0.75rem;
      color: #64748b;
    }
    .cron-error-feedback, .field-error {
      font-size: 0.775rem;
      color: #dc2626;
      font-weight: 500;
    }
    .cron-valid-feedback {
      font-size: 0.775rem;
      color: #16a34a;
      font-weight: 500;
    }
    .alert-error {
      padding: 0.75rem;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 6px;
      color: #b91c1c;
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
    }
    .btn-primary {
      padding: 0.5rem 1.25rem;
      background: #2563eb;
      color: #ffffff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
    }
    .btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDialogComponent {
  private readonly fb = inject(FormBuilder);

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

  onSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const val = this.form.getRawValue();
    const request: ConnectionCreateRequest = {
      name: val.name,
      engine_type: val.engine_type,
      host: val.host,
      port: val.port,
      database_name: val.database_name,
      default_schema: val.default_schema?.trim() || undefined,
      owner: val.owner,
      credentials: {
        username: val.username,
        password: val.password,
      },
      custom_params: {},
      cron_expression: val.cron_expression?.trim() || undefined,
    };

    this.save.emit(request);
  }
}


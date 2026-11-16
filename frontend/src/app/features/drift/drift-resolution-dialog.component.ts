import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  DescriptionDriftDetailResponse,
  DriftResolutionAction,
  DriftService,
  ResolveDescriptionDriftRequest,
} from '../../core/services/drift.service';

@Component({
  selector: 'app-drift-resolution-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './drift-resolution-dialog.component.html',
  styleUrls: ['./drift-resolution-dialog.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DriftResolutionDialogComponent implements OnChanges {
  private readonly driftService = inject(DriftService);

  @Input() tableId: string | null = null;
  @Input() visible = false;
  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() resolved = new EventEmitter<void>();

  readonly isLoading = signal<boolean>(false);
  readonly isResolving = signal<boolean>(false);
  readonly driftData = signal<DescriptionDriftDetailResponse | null>(null);
  readonly selectedAction = signal<DriftResolutionAction>('RETAIN_APPROVED');
  readonly customDescription = signal<string>('');
  readonly errorMessage = signal<string | null>(null);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['visible'] && this.visible && this.tableId) {
      this.loadDrift(this.tableId);
    }
  }

  loadDrift(id: string): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.driftService.getTableDrift(id).subscribe({
      next: (data) => {
        this.driftData.set(data);
        if (data.table_approved_description) {
          this.customDescription.set(data.table_approved_description);
        } else if (data.table_source_description) {
          this.customDescription.set(data.table_source_description);
        }
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در بارگذاری اطلاعات مغایرت توضیحات';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  onSubmitResolution(): void {
    if (!this.tableId) return;

    this.isResolving.set(true);
    this.errorMessage.set(null);

    const action = this.selectedAction();
    const payload: ResolveDescriptionDriftRequest = {
      resolution_action: action,
      custom_description: action === 'CUSTOM_MERGE' ? this.customDescription() : undefined,
      column_resolutions: [],
    };

    this.driftService.resolveDrift(this.tableId, payload).subscribe({
      next: () => {
        this.isResolving.set(false);
        this.resolved.emit();
        this.close();
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در ثبت و حل تعارض توضیحات';
        this.errorMessage.set(msg);
        this.isResolving.set(false);
      },
    });
  }

  close(): void {
    this.visible = false;
    this.visibleChange.emit(false);
  }
}

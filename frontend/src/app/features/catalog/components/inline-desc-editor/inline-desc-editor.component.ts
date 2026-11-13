import {
  ChangeDetectionStrategy,
  Component,
  effect,
  input,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TagChipComponent } from '../tag-chip/tag-chip.component';
import { TagItem } from '../../models/catalog.models';

@Component({
  selector: 'app-inline-desc-editor',
  standalone: true,
  imports: [CommonModule, FormsModule, TagChipComponent],
  templateUrl: './inline-desc-editor.component.html',
  styleUrls: ['./inline-desc-editor.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class InlineDescEditorComponent {
  readonly description = input<string | null>(null);
  readonly placeholder = input<string>('No description provided. Click to add.');
  readonly canEdit = input<boolean>(true);
  readonly canManageTags = input<boolean>(false);
  readonly tags = input<TagItem[]>([]);
  readonly isLoading = input<boolean>(false);
  readonly errorMessage = input<string | null>(null);

  readonly isEditing = signal<boolean>(false);
  readonly currentDescription = signal<string>('');
  readonly editValue = signal<string>('');

  readonly saved = output<string>();
  readonly cancelled = output<void>();
  readonly tagRemoved = output<TagItem>();

  constructor() {
    effect(() => {
      const desc = this.description();
      this.currentDescription.set(desc ?? '');
    });
  }

  startEdit(): void {
    if (!this.canEdit()) return;
    this.editValue.set(this.currentDescription());
    this.isEditing.set(true);
  }

  onInputChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.editValue.set(target.value);
  }

  save(): void {
    const trimmed = this.editValue().trim();
    this.currentDescription.set(trimmed);
    this.isEditing.set(false);
    this.saved.emit(trimmed);
  }

  cancelEdit(): void {
    this.editValue.set(this.currentDescription());
    this.isEditing.set(false);
    this.cancelled.emit();
  }

  onTagRemove(tag: TagItem): void {
    this.tagRemoved.emit(tag);
  }
}

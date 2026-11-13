import {
  ChangeDetectionStrategy,
  Component,
  input,
  output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TagItem } from '../../models/catalog.models';

@Component({
  selector: 'app-tag-chip',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './tag-chip.component.html',
  styleUrls: ['./tag-chip.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TagChipComponent {
  readonly tag = input.required<TagItem>();
  readonly removable = input<boolean>(false);

  readonly removed = output<TagItem>();

  onRemove(event: MouseEvent): void {
    event.stopPropagation();
    this.removed.emit(this.tag());
  }
}


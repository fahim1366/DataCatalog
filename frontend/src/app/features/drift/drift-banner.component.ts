import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-drift-banner',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './drift-banner.component.html',
  styleUrls: ['./drift-banner.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DriftBannerComponent {
  @Input() tableId: string | null = null;
  @Input() tableName: string = '';
  @Input() driftDetected: boolean = false;
  @Output() openResolution = new EventEmitter<void>();

  onResolveClick(): void {
    this.openResolution.emit();
  }
}

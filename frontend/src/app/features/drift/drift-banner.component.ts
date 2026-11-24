import {
  ChangeDetectionStrategy,
  Component,
  output,
  input,
} from "@angular/core";

@Component({
  selector: "app-drift-banner",
  standalone: true,
  imports: [],
  templateUrl: "./drift-banner.component.html",
  styleUrls: ["./drift-banner.component.css"],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DriftBannerComponent {
  readonly tableId = input<string | null>(null);
  readonly tableName = input<string>("");
  readonly driftDetected = input<boolean>(false);
  readonly openResolution = output<void>();

  onResolveClick(): void {
    this.openResolution.emit();
  }
}

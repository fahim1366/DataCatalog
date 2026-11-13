import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { InlineDescEditorComponent } from './inline-desc-editor.component';
import { TagChipComponent } from '../tag-chip/tag-chip.component';
import { TagItem } from '../../models/catalog.models';

describe('InlineDescEditorComponent & TagChipComponent (T047: Inline Curation & Tag Chips)', () => {
  let fixture: ComponentFixture<InlineDescEditorComponent>;
  let component: InlineDescEditorComponent;

  const mockTags: TagItem[] = [
    { id: '11111111-1111-1111-1111-111111111111', name: 'PII', color: '#EF4444', is_governed: true },
    { id: '22222222-2222-2222-2222-222222222222', name: 'Finance', color: '#10B981', is_governed: false },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [InlineDescEditorComponent, TagChipComponent, FormsModule],
    }).compileComponents();

    fixture = TestBed.createComponent(InlineDescEditorComponent);
    component = fixture.componentInstance;
  });

  it('should create the InlineDescEditorComponent instance', () => {
    expect(component).toBeTruthy();
  });

  describe('View Mode & Initial Display', () => {
    it('should display the approved description in read-only mode by default', () => {
      fixture.componentRef.setInput('description', 'Customer orders transaction log.');
      fixture.detectChanges();

      const descText = fixture.debugElement.query(By.css('.desc-display-text'));
      expect(descText).toBeTruthy();
      expect(descText.nativeElement.textContent.trim()).toBe('Customer orders transaction log.');

      const textarea = fixture.debugElement.query(By.css('.desc-editor-textarea'));
      expect(textarea).toBeFalsy();
    });

    it('should show fallback placeholder when description is empty or null', () => {
      fixture.componentRef.setInput('description', null);
      fixture.componentRef.setInput('placeholder', 'No description provided. Click to add.');
      fixture.detectChanges();

      const descText = fixture.debugElement.query(By.css('.desc-display-text'));
      expect(descText).toBeTruthy();
      expect(descText.nativeElement.textContent.trim()).toBe('No description provided. Click to add.');
      expect(descText.classes['placeholder']).toBe(true);
    });

    it('should not allow editing if canEdit is false', () => {
      fixture.componentRef.setInput('description', 'Read only description.');
      fixture.componentRef.setInput('canEdit', false);
      fixture.detectChanges();

      const editBtn = fixture.debugElement.query(By.css('.btn-edit'));
      expect(editBtn).toBeFalsy();

      // Clicking text should not enter edit mode
      const descContainer = fixture.debugElement.query(By.css('.desc-container'));
      descContainer.nativeElement.click();
      fixture.detectChanges();

      expect(component.isEditing()).toBe(false);
    });
  });

  describe('Inline Click-to-Edit', () => {
    it('should switch to edit mode when clicking the edit button or description container', () => {
      fixture.componentRef.setInput('description', 'Original curated description.');
      fixture.componentRef.setInput('canEdit', true);
      fixture.detectChanges();

      const editBtn = fixture.debugElement.query(By.css('.btn-edit'));
      expect(editBtn).toBeTruthy();
      editBtn.nativeElement.click();
      fixture.detectChanges();

      expect(component.isEditing()).toBe(true);
      const textarea = fixture.debugElement.query(By.css('.desc-editor-textarea'));
      expect(textarea).toBeTruthy();
      expect(textarea.nativeElement.value).toBe('Original curated description.');
    });
  });

  describe('Save Dispatch', () => {
    it('should dispatch save event with trimmed updated text and exit edit mode', () => {
      fixture.componentRef.setInput('description', 'Initial description.');
      fixture.componentRef.setInput('canEdit', true);
      fixture.detectChanges();

      // Enter edit mode
      component.startEdit();
      fixture.detectChanges();

      const saveSpy = vi.fn();
      component.saved.subscribe(saveSpy);

      const textarea = fixture.debugElement.query(By.css('.desc-editor-textarea'));
      textarea.nativeElement.value = 'Updated curated description with compliance details.';
      textarea.nativeElement.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const saveBtn = fixture.debugElement.query(By.css('.btn-save'));
      expect(saveBtn).toBeTruthy();
      saveBtn.nativeElement.click();
      fixture.detectChanges();

      expect(saveSpy).toHaveBeenCalledWith('Updated curated description with compliance details.');
      expect(component.isEditing()).toBe(false);

      // Displayed text should reflect updated value
      const descText = fixture.debugElement.query(By.css('.desc-display-text'));
      expect(descText.nativeElement.textContent.trim()).toBe('Updated curated description with compliance details.');
    });
  });

  describe('Cancel Rollback', () => {
    it('should rollback edits to original value and exit edit mode when cancel is clicked', () => {
      fixture.componentRef.setInput('description', 'Original unmodified text.');
      fixture.componentRef.setInput('canEdit', true);
      fixture.detectChanges();

      component.startEdit();
      fixture.detectChanges();

      const cancelSpy = vi.fn();
      component.cancelled.subscribe(cancelSpy);

      const textarea = fixture.debugElement.query(By.css('.desc-editor-textarea'));
      textarea.nativeElement.value = 'Changed text that should be abandoned.';
      textarea.nativeElement.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const cancelBtn = fixture.debugElement.query(By.css('.btn-cancel'));
      expect(cancelBtn).toBeTruthy();
      cancelBtn.nativeElement.click();
      fixture.detectChanges();

      expect(cancelSpy).toHaveBeenCalled();
      expect(component.isEditing()).toBe(false);

      const descText = fixture.debugElement.query(By.css('.desc-display-text'));
      expect(descText.nativeElement.textContent.trim()).toBe('Original unmodified text.');
    });
  });

  describe('Tag Chip Rendering', () => {
    it('should render TagChipComponent for each tag passed in tags input', () => {
      fixture.componentRef.setInput('description', 'Table description.');
      fixture.componentRef.setInput('tags', mockTags);
      fixture.detectChanges();

      const chips = fixture.debugElement.queryAll(By.directive(TagChipComponent));
      expect(chips.length).toBe(2);

      const firstChip = chips[0].nativeElement;
      expect(firstChip.textContent).toContain('PII');

      const secondChip = chips[1].nativeElement;
      expect(secondChip.textContent).toContain('Finance');
    });

    it('should dispatch tagRemoved event when chip remove button is clicked', () => {
      fixture.componentRef.setInput('tags', mockTags);
      fixture.componentRef.setInput('canManageTags', true);
      fixture.detectChanges();

      const removeSpy = vi.fn();
      component.tagRemoved.subscribe(removeSpy);

      const firstChip = fixture.debugElement.query(By.directive(TagChipComponent));
      const removeBtn = firstChip.query(By.css('.tag-remove-btn'));
      expect(removeBtn).toBeTruthy();
      removeBtn.nativeElement.click();
      fixture.detectChanges();

      expect(removeSpy).toHaveBeenCalledWith(mockTags[0]);
    });
  });

  describe('TagChipComponent (Standalone)', () => {
    let chipFixture: ComponentFixture<TagChipComponent>;
    let chipComponent: TagChipComponent;

    beforeEach(() => {
      chipFixture = TestBed.createComponent(TagChipComponent);
      chipComponent = chipFixture.componentInstance;
    });

    it('should create standalone TagChipComponent', () => {
      expect(chipComponent).toBeTruthy();
    });

    it('should render tag name and apply custom tag color', () => {
      chipFixture.componentRef.setInput('tag', mockTags[0]);
      chipFixture.detectChanges();

      const chipEl = chipFixture.debugElement.query(By.css('.tag-chip'));
      expect(chipEl.nativeElement.textContent).toContain('PII');
      expect(chipEl.nativeElement.style.borderColor).toBe('rgb(239, 68, 68)');
    });

    it('should render remove button when removable is true and emit removed event', () => {
      chipFixture.componentRef.setInput('tag', mockTags[1]);
      chipFixture.componentRef.setInput('removable', true);
      chipFixture.detectChanges();

      const removedSpy = vi.fn();
      chipComponent.removed.subscribe(removedSpy);

      const removeBtn = chipFixture.debugElement.query(By.css('.tag-remove-btn'));
      expect(removeBtn).toBeTruthy();
      removeBtn.nativeElement.click();
      chipFixture.detectChanges();

      expect(removedSpy).toHaveBeenCalledWith(mockTags[1]);
    });
  });
});


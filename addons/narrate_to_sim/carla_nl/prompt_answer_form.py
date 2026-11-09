"""Small inline Tk forms embedded in the Review text widget."""
import tkinter as tk
from tkinter import ttk

from .prompt_answers import answer_fields, fill_answers


class PromptAnswerForm(tk.Frame):
    def __init__(self, parent, template, colors, on_apply, *, auto_choice=True, question=None, context='', field_info=None, prompt=None):
        super().__init__(parent, bg=colors['surface'], padx=0, pady=4)
        self.template, self.colors, self.on_apply = template, colors, on_apply
        self.fields = answer_fields(template, prompt=prompt)
        if field_info and len(self.fields) == 1:
            from dataclasses import replace
            self.fields = [replace(self.fields[0], question=field_info.question, hint=field_info.hint,
                                   number_kind=field_info.number_kind, allow_ego=field_info.allow_ego)]
        if question and len(self.fields) == 1:
            from dataclasses import replace
            self.fields = [replace(self.fields[0], question=question)]
        self.auto_choice = auto_choice
        self.values, self.units, self.choice_buttons = {}, {}, {}
        self.inputs = []
        self.field_inputs = {}
        self.feedback = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.question_labels = []
        self.hint_labels = []
        self.field_labels = {}
        self.field_hint_labels = {}
        self.context_label = tk.Label(self, text=context, bg=colors['surface'], fg=colors['text_muted'],
                                     font=('Segoe UI', 10), anchor='w', justify='left', wraplength=390)
        if context:
            self.context_label.grid(row=0, column=0, sticky='w', pady=(0, 4))
        self.question_labels.append(self.context_label)
        for index, field in enumerate(self.fields):
            label = tk.Label(self, text=field.question, bg=colors['surface'], fg=colors['text'],
                             font=('Segoe UI', 10, 'bold'), anchor='w', justify='left')
            label.grid(row=index * 3 + 1, column=0, sticky='w', pady=(5, 3))
            self.question_labels.append(label)
            self.field_labels[field.key] = label
            if field.hint:
                hint = tk.Label(self, text=field.hint, bg=colors['surface'], fg=colors['text_muted'],
                                font=('Segoe UI', 10), anchor='w', justify='left')
                hint.grid(row=index * 3 + 2, column=0, sticky='w', pady=(0, 7))
                self.hint_labels.append(hint)
                self.field_hint_labels[field.key] = hint
            row = tk.Frame(self, bg=colors['surface'])
            row.grid(row=index * 3 + 3, column=0, sticky='w', pady=(0, 5))
            self.values[field.key] = tk.StringVar(self)
            self.units[field.key] = tk.StringVar(self, value=field.unit)
            if field.choices:
                for n, (value, text) in enumerate(field.choices):
                    button = tk.Button(row, text=text, relief='flat', cursor='hand2',
                        font=('Segoe UI', 10), padx=10, pady=5,
                        bg=colors['surface_alt'], fg=colors['text'],
                        activebackground=colors['surface_selected'], activeforeground=colors['text'],
                        command=lambda f=field, v=value: self.choose(f, v))
                    button.grid(row=n // 3, column=n % 3, padx=(0, 5), pady=(0, 4), sticky='w')
                    self.choice_buttons[(field.key, value)] = button
                    self.inputs.append(button)
                    self.field_inputs.setdefault(field.key, button)
            else:
                entry = tk.Entry(row, textvariable=self.values[field.key], width=14,
                                 bg=colors['field'], fg=colors['text'], insertbackground=colors['text'],
                                 relief='solid', bd=1, font=('Segoe UI', 11))
                entry.grid(row=0, column=0, ipady=4, padx=(0, 8))
                entry.bind('<Return>', lambda event: self.submit())
                self.inputs.append(entry)
                self.field_inputs[field.key] = entry
                if len(field.units) > 1:
                    control = ttk.Combobox(row, textvariable=self.units[field.key], values=field.units,
                                           width=6, state='readonly')
                    control.grid(row=0, column=1)
                    self.inputs.append(control)
                elif field.unit:
                    tk.Label(row, text=field.unit, bg=colors['surface'], fg=colors['text_muted'],
                             font=('Segoe UI', 10)).grid(row=0, column=1)
        self.apply_button = tk.Button(self, text='Update description', command=self.submit,
            bg=colors['brand_tint'], fg=colors['brand_hover'], relief='flat', padx=12, pady=6,
            activebackground=colors['surface_selected'], activeforeground=colors['text'],
            font=('Segoe UI', 10, 'bold'), cursor='hand2')
        self.apply_button.grid(row=len(self.fields) * 3 + 1, column=0, sticky='w', pady=(4, 2))
        if auto_choice and len(self.fields) == 1 and self.fields[0].choices:
            self.apply_button.grid_remove()
            self.feedback.set('Select an option to update your description.')
        else:
            self.feedback.set('Complete these fields, then update your description.')
        self.feedback_label = tk.Label(self, textvariable=self.feedback, bg=colors['surface'],
            fg=colors['text_muted'], font=('Segoe UI', 10), justify='left', anchor='w', wraplength=390)
        self.feedback_label.grid(row=len(self.fields) * 3 + 2, column=0, sticky='w', pady=(4, 0))
        self._resize_binding = parent.bind('<Configure>', self._wrap, add='+')
        self.bind('<Map>', self._wrap)
        self.bind('<Destroy>', self._release_resize_binding)
        self._wrap()

    def _wrap(self, event=None):
        # Text windows keep their requested width. Fix the available column
        # width so wrapping does not repeatedly shrink the form to its inputs.
        width = max(300, self.master.winfo_width() - 50)
        self.columnconfigure(0, minsize=width)
        for label in self.question_labels + self.hint_labels + [self.feedback_label]:
            label.configure(wraplength=width - 12)

    def _release_resize_binding(self, event):
        if event.widget == self:
            self.master.unbind('<Configure>', self._resize_binding)

    def choose(self, field, value):
        self.values[field.key].set(value)
        for (key, option), button in self.choice_buttons.items():
            if key == field.key:
                selected = option == value
                button.configure(bg=self.colors['brand_tint'] if selected else self.colors['surface_alt'],
                                 fg=self.colors['brand_hover'] if selected else self.colors['text'],
                                 relief='solid' if selected else 'flat', bd=1 if selected else 0)
        if self.auto_choice and len(self.fields) == 1:
            self.submit()

    def refresh_number_hints(self, prompt, field_info=None):
        """Refresh label examples and reference hints after another answer."""
        from dataclasses import replace
        fresh = answer_fields(self.template, prompt=prompt)
        if field_info is not None and len(fresh) == 1:
            fresh = [field_info]
        for index, (old, new) in enumerate(zip(self.fields, fresh)):
            if old.key[0] not in ('number', 'vehicle', 'obstacle', 'reference vehicle') or not new.hint:
                continue
            self.fields[index] = replace(old, question=new.question, hint=new.hint,
                                         number_kind=new.number_kind, allow_ego=new.allow_ego)
            self.field_labels[old.key].configure(text=new.question)
            self.field_hint_labels[old.key].configure(text=new.hint)

    def submit(self):
        for label in self.field_labels.values():
            label.configure(fg=self.colors['text'])
        answers = {f.key: (self.values[f.key].get(), self.units[f.key].get()) for f in self.fields}
        try:
            fill_answers(self.template, answers, fields=self.fields)
        except ValueError as exc:
            self.feedback.set(str(exc))
            self.feedback_label.configure(fg=self.colors['warning_text'])
            key = getattr(exc, 'field_key', None)
            control = self.field_inputs.get(key)
            if control is not None:
                control.focus_set()
                self.field_labels[key].configure(fg=self.colors['warning_text'])
                if isinstance(control, tk.Entry):
                    control.selection_range(0, 'end')
            return 'break'
        if self.on_apply(answers) and self.winfo_exists():
            self.feedback.set('Updated in your description. Ctrl+Z to undo.')
            self.feedback_label.configure(fg=self.colors['success'])
        return 'break'

    def set_applied(self, applied):
        for control in self.inputs:
            control.configure(state='disabled' if applied else 'readonly' if isinstance(control, ttk.Combobox) else 'normal')
        self.apply_button.configure(state='disabled' if applied else 'normal')
        if not applied:
            self.feedback.set('Complete these fields, then update your description.')

import { forwardRef } from "react";
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

function joinClasses(...classes: Array<string | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export const UiButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(
  function UiButton({ className, type = "button", ...props }, ref) {
    return <button ref={ref} className={joinClasses("ui-button", className)} type={type} {...props} />;
  },
);

export const UiInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function UiInput({ className, type, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={joinClasses("ui-control", type === "checkbox" ? "ui-checkbox" : "ui-input", className)}
        type={type}
        {...props}
      />
    );
  },
);

export const UiSelect = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function UiSelect({ className, children, ...props }, ref) {
    return (
      <select ref={ref} className={joinClasses("ui-control", "ui-select", className)} {...props}>
        {children}
      </select>
    );
  },
);

export const UiTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function UiTextarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={joinClasses("ui-control", "ui-textarea", className)} {...props} />;
  },
);

import clsx from "clsx";
import { useId } from "react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const CONTROL =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg placeholder:text-muted";

interface FieldShellProps {
  label: string;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
}

function FieldShell({ label, hint, children }: FieldShellProps) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function TextField({
  label,
  hint,
  className,
  ...rest
}: { label: string; hint?: ReactNode } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <FieldShell label={label} hint={hint}>
      {(id) => <input id={id} className={clsx(CONTROL, className)} {...rest} />}
    </FieldShell>
  );
}

export function SelectField({
  label,
  hint,
  className,
  children,
  ...rest
}: { label: string; hint?: ReactNode } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <FieldShell label={label} hint={hint}>
      {(id) => (
        <select id={id} className={clsx(CONTROL, className)} {...rest}>
          {children}
        </select>
      )}
    </FieldShell>
  );
}

export function TextAreaField({
  label,
  hint,
  className,
  ...rest
}: { label: string; hint?: ReactNode } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <FieldShell label={label} hint={hint}>
      {(id) => <textarea id={id} className={clsx(CONTROL, "font-mono", className)} {...rest} />}
    </FieldShell>
  );
}

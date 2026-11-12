"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, endpoints } from "@/lib/api";
import { Button, Field, Notice } from "@/components/ui";

const MIN_PASSWORD = 12;

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const isRegister = mode === "register";
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(
    null,
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setFieldError(null);
    try {
      if (isRegister) {
        await endpoints.register({ email, name, password });
      } else {
        await endpoints.login({ email, password });
      }
      router.push("/projects");
    } catch (e) {
      if (e instanceof ApiError) {
        // The API's stable codes let us put the message on the right field
        // rather than dumping one banner for everything.
        if (e.code === "weak_password") {
          setFieldError({ field: "password", message: e.detail ?? e.message });
        } else if (e.code === "email_taken") {
          setFieldError({ field: "email", message: e.message_for_user });
        } else {
          setError(e.message_for_user);
        }
      } else {
        setError("Could not reach the server. Is the API running?");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <Link href="/" className="mb-10 inline-flex items-baseline gap-2">
        <span className="display text-lg">BIOSTRUCT</span>
        <span className="text-[10px] uppercase tracking-[0.2em] text-faint">
          protein design
        </span>
      </Link>

      <h1 className="display text-3xl">
        {isRegister ? "Create an account" : "Sign in"}
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-muted">
        {isRegister
          ? "Projects, targets and design runs are private to your account."
          : "Welcome back."}
      </p>

      {error && (
        <div className="mt-6">
          <Notice tone="danger" title={error} />
        </div>
      )}

      {/* method="post" matters even though submission goes through fetch: if
          JS has not hydrated, the browser falls back to a native submit, and
          the default GET would put the password in the URL — and therefore in
          history, logs and the referrer. A POST keeps it in the body. */}
      <form
        onSubmit={submit}
        method="post"
        action=""
        className="mt-8 space-y-5"
        noValidate
      >
        {isRegister && (
          <Field
            label="Name"
            name="name"
            autoComplete="name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ada Okafor"
          />
        )}
        <Field
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@university.edu"
          error={fieldError?.field === "email" ? fieldError.message : undefined}
        />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete={isRegister ? "new-password" : "current-password"}
          required
          minLength={isRegister ? MIN_PASSWORD : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={
            isRegister
              ? `At least ${MIN_PASSWORD} characters. A phrase of a few words beats a short, complicated one.`
              : undefined
          }
          error={fieldError?.field === "password" ? fieldError.message : undefined}
        />

        <Button type="submit" variant="primary" disabled={busy} className="w-full py-2.5">
          {busy ? "Working…" : isRegister ? "Create account" : "Sign in"}
        </Button>
      </form>

      <p className="mt-6 text-sm text-muted">
        {isRegister ? (
          <>
            Already have an account?{" "}
            <Link href="/login" className="text-signal underline-offset-4 hover:underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            No account yet?{" "}
            <Link href="/register" className="text-signal underline-offset-4 hover:underline">
              Create one
            </Link>
          </>
        )}
      </p>
    </main>
  );
}

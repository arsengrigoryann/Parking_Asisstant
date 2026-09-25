"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot,
  CarFront,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRound,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Message = { id: string; role: "user" | "assistant"; content: string };

type Workflow = {
  response?: string | null;
  route?: string | null;
  reservation_collection_status?: string | null;
  missing_fields?: string[];
  reservation_id?: string | null;
  status?: string | null;
  recording_status?: string | null;
  final_message?: string | null;
  waiting_for_human?: boolean;
};

type Reservation = {
  reservation_id: string;
  first_name: string;
  last_name: string;
  car_number: string;
  start_datetime: string;
  end_datetime: string;
  facility_id: string;
  facility_name: string;
  status: string;
  rejection_reason?: string | null;
};

type Review = {
  reservation: Reservation;
  brief: { summary: string; review_notice: string };
};

const welcome: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "Hello! Ask about parking location, policies, live availability, hours, pricing, or start a reservation.",
};

export function ParkingDemo() {
  const [sessionId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<Message[]>([welcome]);
  const [input, setInput] = useState("");
  const [workflow, setWorkflow] = useState<Workflow>({});
  const [review, setReview] = useState<Review | null>(null);
  const [rejectionReason, setRejectionReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  useEffect(() => {
    if (!workflow.reservation_id || !workflow.waiting_for_human) return;
    const timer = window.setInterval(() => void refreshStatus(), 4000);
    return () => window.clearInterval(timer);
  }, [workflow.reservation_id, workflow.waiting_for_human]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: message },
    ]);
    try {
      const response = await fetch("/api/parking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message }),
      });
      const result = await parse<Workflow>(response);
      setWorkflow(result);
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content:
            result.response || result.final_message || "Request completed.",
        },
      ]);
    } catch (caught) {
      setError(messageFor(caught));
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatus() {
    if (!workflow.reservation_id) return;
    try {
      const response = await fetch(
        `/api/parking/reservations/${workflow.reservation_id}`,
        { cache: "no-store" },
      );
      const result = await parse<Workflow>(response);
      setWorkflow((current) => ({ ...current, ...result }));
    } catch (caught) {
      setError(messageFor(caught));
    }
  }

  async function loadReview() {
    if (!workflow.reservation_id) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/parking/reservations/${workflow.reservation_id}?view=review`,
        { cache: "no-store" },
      );
      setReview(await parse<Review>(response));
    } catch (caught) {
      setError(messageFor(caught));
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: "approve" | "reject" | "retry") {
    if (!workflow.reservation_id) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/parking/reservations/${workflow.reservation_id}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action,
            reason: rejectionReason.trim() || null,
          }),
        },
      );
      const result = await parse<{
        reservation: Reservation;
        workflow: Workflow;
      }>(response);
      setWorkflow(result.workflow);
      if (review && action !== "retry") {
        setReview({ ...review, reservation: result.reservation });
      }
      const final = result.workflow.final_message;
      if (final) {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: "assistant", content: final },
        ]);
      }
    } catch (caught) {
      setError(messageFor(caught));
    } finally {
      setBusy(false);
    }
  }

  const pending = workflow.status === "PENDING_APPROVAL";
  const failedRecording = workflow.recording_status === "retryable_failure";

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_#dff7ef,_transparent_34%),linear-gradient(135deg,#f7faf8_0%,#eef3f0_100%)] p-4 text-slate-900 md:p-8">
      <div className="mx-auto grid max-w-7xl gap-5 lg:grid-cols-[minmax(0,1fr)_390px]">
        <section className="flex min-h-[calc(100vh-4rem)] flex-col overflow-hidden rounded-3xl border border-white/70 bg-white/90 shadow-xl shadow-emerald-950/5 backdrop-blur">
          <header className="flex items-center justify-between border-b px-5 py-4 md:px-7">
            <div className="flex items-center gap-3">
              <div className="grid size-11 place-items-center rounded-2xl bg-emerald-700 text-white">
                <CarFront />
              </div>
              <div>
                <h1 className="text-lg font-semibold">Parking Assistant</h1>
                <p className="text-sm text-slate-500">
                  Information, reservations, and human approval
                </p>
              </div>
            </div>
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800">
              Local demo
            </span>
          </header>

          <div
            ref={scrollRef}
            className="flex-1 space-y-5 overflow-y-auto p-5 md:p-7"
          >
            {messages.map((message) => (
              <div
                key={message.id}
                className={cn(
                  "flex gap-3",
                  message.role === "user" && "justify-end",
                )}
              >
                {message.role === "assistant" && (
                  <Avatar icon={<Bot className="size-4" />} />
                )}
                <div
                  className={cn(
                    "max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-6 whitespace-pre-wrap",
                    message.role === "user"
                      ? "bg-slate-900 text-white"
                      : "border bg-slate-50 text-slate-700",
                  )}
                >
                  {message.content}
                </div>
                {message.role === "user" && (
                  <Avatar
                    icon={<UserRound className="size-4" />}
                    dark
                  />
                )}
              </div>
            ))}
            {busy && <p className="pl-12 text-sm text-slate-400">Working…</p>}
          </div>

          <form
            onSubmit={send}
            className="border-t bg-white p-4 md:p-6"
          >
            {error && (
              <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </p>
            )}
            <div className="flex items-end gap-3">
              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="Ask a question or start a reservation…"
                className="min-h-12 resize-none rounded-2xl"
                aria-label="Message"
              />
              <Button
                type="submit"
                size="icon"
                className="size-12 rounded-2xl"
                disabled={busy || !input.trim()}
                aria-label="Send message"
              >
                <Send />
              </Button>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              Reservation details stay outside durable graph checkpoints.
            </p>
          </form>
        </section>

        <aside className="space-y-5">
          <WorkflowCard
            workflow={workflow}
            onRefresh={() => void refreshStatus()}
          />

          {workflow.reservation_id && (
            <Card className="border-emerald-100 bg-white/95">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ShieldCheck className="size-5 text-emerald-700" /> Human
                  review
                </CardTitle>
                <CardDescription>
                  Authorization is enforced by the authenticated administrator
                  API and PostgreSQL—not this browser.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {!review ? (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => void loadReview()}
                    disabled={busy}
                  >
                    Load authenticated review
                  </Button>
                ) : (
                  <ReviewPanel review={review} />
                )}
                {pending && review && (
                  <div className="space-y-3 border-t pt-4">
                    <Textarea
                      value={rejectionReason}
                      onChange={(event) =>
                        setRejectionReason(event.target.value)
                      }
                      placeholder="Optional rejection reason"
                    />
                    <div className="grid grid-cols-2 gap-2">
                      <Button
                        onClick={() => void decide("approve")}
                        disabled={busy}
                      >
                        Approve
                      </Button>
                      <Button
                        variant="destructive"
                        onClick={() => void decide("reject")}
                        disabled={busy}
                      >
                        Reject
                      </Button>
                    </div>
                  </div>
                )}
                {failedRecording && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => void decide("retry")}
                    disabled={busy}
                  >
                    <RefreshCw /> Retry MCP recording
                  </Button>
                )}
              </CardContent>
            </Card>
          )}
        </aside>
      </div>
    </main>
  );
}

function WorkflowCard({
  workflow,
  onRefresh,
}: {
  workflow: Workflow;
  onRefresh: () => void;
}) {
  const label =
    workflow.status || workflow.reservation_collection_status || "Ready";
  return (
    <Card className="bg-slate-950 text-white">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>Workflow status</CardTitle>
            <CardDescription className="mt-2 text-slate-400">
              Authoritative progress from the master graph
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onRefresh}
            disabled={!workflow.reservation_id}
            aria-label="Refresh status"
          >
            <RefreshCw />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <StatusRow
          icon={<Clock3 />}
          label="Lifecycle"
          value={formatLabel(label)}
        />
        <StatusRow
          icon={<CheckCircle2 />}
          label="Recording"
          value={formatLabel(workflow.recording_status || "not started")}
        />
        {workflow.reservation_id && (
          <p className="rounded-xl bg-white/5 p-3 font-mono text-xs break-all text-slate-300">
            {workflow.reservation_id}
          </p>
        )}
        {workflow.final_message && (
          <p className="text-slate-300">{workflow.final_message}</p>
        )}
      </CardContent>
    </Card>
  );
}

function ReviewPanel({ review }: { review: Review }) {
  const item = review.reservation;
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-emerald-50/60 p-4">
        <p className="mb-3 text-xs font-semibold tracking-wide text-emerald-800 uppercase">
          Authoritative PostgreSQL fields
        </p>
        <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-slate-500">Customer</dt>
          <dd>
            {item.first_name} {item.last_name}
          </dd>
          <dt className="text-slate-500">Car number</dt>
          <dd>{item.car_number}</dd>
          <dt className="text-slate-500">Facility</dt>
          <dd>{item.facility_name}</dd>
          <dt className="text-slate-500">Start</dt>
          <dd>{formatDate(item.start_datetime)}</dd>
          <dt className="text-slate-500">End</dt>
          <dd>{formatDate(item.end_datetime)}</dd>
          <dt className="text-slate-500">Status</dt>
          <dd className="font-medium">{formatLabel(item.status)}</dd>
        </dl>
      </div>
      <div className="rounded-xl border border-dashed p-4 text-sm text-slate-600">
        <p className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Generated assistance — non-authoritative
        </p>
        <p className="mb-2 font-medium text-slate-800">
          Facility: {item.facility_name}
        </p>
        <p>{review.brief.summary}</p>
        <p className="mt-2 text-xs text-slate-400">
          {review.brief.review_notice}
        </p>
      </div>
    </div>
  );
}

function StatusRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-emerald-400 [&>svg]:size-4">{icon}</span>
      <span className="text-slate-400">{label}</span>
      <span className="ml-auto font-medium">{value}</span>
    </div>
  );
}

function Avatar({
  icon,
  dark = false,
}: {
  icon: React.ReactNode;
  dark?: boolean;
}) {
  return (
    <div
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-full",
        dark ? "bg-slate-900 text-white" : "bg-emerald-100 text-emerald-800",
      )}
    >
      {icon}
    </div>
  );
}

async function parse<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(body.detail || "Request failed.");
  return body as T;
}

function messageFor(caught: unknown): string {
  return caught instanceof Error
    ? caught.message
    : "Unexpected request failure.";
}

function formatLabel(value: string): string {
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^./, (letter) => letter.toUpperCase());
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

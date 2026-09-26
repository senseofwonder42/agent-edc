import type { RemoteThreadListAdapter, ThreadMessage } from "@assistant-ui/react";
import type { Client, Thread } from "@langchain/langgraph-sdk";
import { createAssistantStream } from "assistant-stream";

// Conversation list backed by the server's threads (Aegra). Without it the
// runtime falls back to an in-memory list that is empty on every page load.
// Title, archive flag: stored in the thread metadata.

const TITLE_MAX = 60;

const toMetadata = (thread: Thread) => {
  const metadata = (thread.metadata ?? {}) as Record<string, unknown>;
  const title = typeof metadata.thread_name === "string" ? metadata.thread_name : "";
  return {
    status: metadata.archived === true ? ("archived" as const) : ("regular" as const),
    remoteId: thread.thread_id,
    externalId: thread.thread_id,
    title: title || undefined,
    lastMessageAt: new Date(thread.updated_at),
  };
};

const firstUserText = (messages: readonly ThreadMessage[]) => {
  const first = messages.find((m) => m.role === "user");
  const text = first?.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join(" ")
    .trim();
  if (!text) return undefined;
  return text.length > TITLE_MAX ? `${text.slice(0, TITLE_MAX - 1)}…` : text;
};

export function createThreadListAdapter(client: Client): RemoteThreadListAdapter {
  const setMetadata = async (threadId: string, patch: Record<string, unknown>) => {
    await client.threads.update(threadId, { metadata: patch });
  };

  return {
    async list() {
      const threads = await client.threads.search({
        limit: 100,
        sortBy: "updated_at",
        sortOrder: "desc",
      });
      return { threads: threads.map(toMetadata) };
    },
    async initialize() {
      const { thread_id } = await client.threads.create();
      return { remoteId: thread_id, externalId: thread_id };
    },
    async fetch(threadId) {
      return toMetadata(await client.threads.get(threadId));
    },
    rename: (threadId, title) => setMetadata(threadId, { thread_name: title }),
    archive: (threadId) => setMetadata(threadId, { archived: true }),
    unarchive: (threadId) => setMetadata(threadId, { archived: false }),
    delete: (threadId) => client.threads.delete(threadId),
    // Title = first question: streamed to the list at once, persisted in the
    // metadata before the stream completes (the adapter contract).
    async generateTitle(threadId, messages) {
      const title = firstUserText(messages);
      return createAssistantStream(async (controller) => {
        if (!title) return;
        await setMetadata(threadId, { thread_name: title });
        controller.appendText(title);
      });
    },
  };
}

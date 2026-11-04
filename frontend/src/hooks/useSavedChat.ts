import { useEffect, useRef, useState } from "react";

import { useAuth } from "../context/AuthContext";

type ApiProperty = {
  id: number;
  title: string;
  location: string;
  price: number;
  bedrooms: number;
  image: string;
  area_sqft: number;
  property_type: string;
};

export type Property = {
  id: number;
  title: string;
  location: string;
  price: string;
  details: string;
  image: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  properties?: Property[];
};

type SavedMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  properties: ApiProperty[];
};

type Conversation = {
  id: string;
  title: string;
  updated_at: string;
};

type ConversationDetail = {
  id: string;
  messages: SavedMessage[];
};

function makeCards(listings: ApiProperty[]): Property[] {
  return listings.map((property) => ({
    id: property.id,
    title: property.title,
    location: property.location,
    price: `₹${property.price.toLocaleString("en-IN")}`,
    details: [
      `${property.bedrooms} BHK`,
      `${property.area_sqft.toLocaleString("en-IN")} sqft`,
      property.property_type === "independent_house"
        ? "Independent house"
        : "Flat",
    ].join(" • "),
    image: property.image,
  }));
}

function makeMessages(messages: SavedMessage[]): Message[] {
  return messages.map((message) => ({
    id: String(message.id),
    role: message.role,
    text: message.content,
    properties: makeCards(message.properties ?? []),
  }));
}

export function useSavedChat() {
  const { authFetch, token, ready } = useAuth();

  const fetchRef = useRef(authFetch);
  fetchRef.current = authFetch;

  const busyRef = useRef(false);

  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [error, setError] = useState("");

  const signedIn = Boolean(token);

  async function request(path: string, options?: RequestInit) {
    const response = await fetchRef.current(path, options);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Request failed (${response.status}).`,
      );
    }

    return data;
  }

  useEffect(() => {
    if (!ready) return;

    let active = true;

    if (!signedIn) {
      setMessages([]);
      setConversations([]);
      setConversationId(null);
      setError("");
      setRestoring(false);
      return;
    }

    setRestoring(true);
    setError("");

    async function restore() {
      try {
        const response = await fetchRef.current("/conversations");
        const data = await response.json();

        if (!response.ok) {
          throw new Error(
            typeof data.detail === "string"
              ? data.detail
              : "Could not load saved chats.",
          );
        }

        if (!active) return;

        const history: Conversation[] = data.conversations;
        setConversations(history);

        if (history.length > 0) {
          const chatResponse = await fetchRef.current(
            `/conversations/${history[0].id}`,
          );

          const chat: ConversationDetail & { detail?: string } =
            await chatResponse.json();

          if (!chatResponse.ok) {
            throw new Error(
              chat.detail ?? "Could not restore your latest chat.",
            );
          }

          if (!active) return;

          setConversationId(chat.id);
          setMessages(makeMessages(chat.messages));
        }
      } catch (cause) {
        if (active) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not restore saved chats.",
          );
        }
      } finally {
        if (active) setRestoring(false);
      }
    }

    void restore();

    return () => {
      active = false;
    };
  }, [ready, signedIn]);

  async function openConversation(id: string) {
    if (busyRef.current || restoring) return;

    busyRef.current = true;
    setLoading(true);
    setError("");

    try {
      const data: ConversationDetail = await request(
        `/conversations/${id}`,
      );

      setConversationId(data.id);
      setMessages(makeMessages(data.messages));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not open chat.",
      );
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }

  async function send(text: string): Promise<boolean> {
    const message = text.trim();

    if (!message || busyRef.current || restoring) return false;

    busyRef.current = true;
    setLoading(true);
    setError("");

    try {
      let id = conversationId;

      if (!id) {
        const created: { id: string } = await request(
          "/conversations",
          { method: "POST" },
        );

        id = created.id;
        setConversationId(id);
      }

      const data = await request("/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          conversation_id: id,
        }),
      });

      setMessages((previous) => [
        ...previous,
        {
          id: String(data.user_message_id),
          role: "user",
          text: message,
        },
        {
          id: String(data.assistant_message_id),
          role: "assistant",
          text: data.reply,
          properties: makeCards(data.properties ?? []),
        },
      ]);

      try {
        const history = await request("/conversations");
        setConversations(history.conversations);
      } catch {
        setError(
          "Message saved. Reopen LEIA to refresh the history list.",
        );
      }

      return true;
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not send message.",
      );

      return false;
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }

  function newConversation() {
    if (busyRef.current || restoring) return;

    setConversationId(null);
    setMessages([]);
    setError("");
  }

  return {
    messages,
    conversations,
    loading,
    restoring,
    error,
    send,
    openConversation,
    newConversation,
  };
}
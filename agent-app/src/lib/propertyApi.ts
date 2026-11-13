export type PropertySummary = {
  id: string;
  title: string;
  property_type: string;
  price: number | null;
  locality: string | null;
  city: string | null;
  status: string;
  availability: string;
  version: number;
};

export async function readResponse(response: Response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error(
        "Your session has expired. Copy any unsaved notes before signing out and signing in again.",
      );
    }
    if (response.status === 403)
      throw new Error("Your account does not have access to this action.");
    if (Array.isArray(data?.detail)) {
      throw new Error(
        data.detail
          .map(
            (item: { loc?: (string | number)[]; msg?: string }) =>
              `${item.loc?.slice(1).join(".") || "Form"}: ${item.msg || "Invalid value"}`,
          )
          .join("\n"),
      );
    }
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `Request failed (${response.status}).`,
    );
  }
  if (data === null)
    throw new Error("The server returned an invalid response.");
  return data;
}

export function errorMessage(error: unknown) {
  return error instanceof TypeError
    ? "Could not reach LEIA. Check your connection and backend."
    : error instanceof Error
      ? error.message
      : "Something went wrong. Please try again.";
}

export function isProperty(value: unknown): value is PropertySummary {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.id === "string" &&
    typeof item.version === "number" &&
    Number.isSafeInteger(item.version) &&
    typeof item.title === "string" &&
    typeof item.property_type === "string" &&
    typeof item.status === "string" &&
    typeof item.availability === "string" &&
    (item.price === null ||
      (typeof item.price === "number" && Number.isFinite(item.price))) &&
    (item.locality === null || typeof item.locality === "string") &&
    (item.city === null || typeof item.city === "string")
  );
}

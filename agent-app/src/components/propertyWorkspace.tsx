import type { StaffFetch } from "../lib/staffSession";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";

import {
  errorMessage,
  isProperty,
  PropertySummary,
  readResponse,
} from "../lib/propertyApi";
import { styles as s } from "../styles/property.styles";
import PropertyDraftForm from "./PropertyDraftForm";

type Props = {
  authFetch: StaffFetch;
  role: "agent" | "admin";
  onBusyChange: (busy: boolean) => void;
};

export default function PropertyWorkspace({
  authFetch,
  role,
  onBusyChange,
}: Props) {
  const [properties, setProperties] = useState<PropertySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [editor, setEditor] = useState<{ id: string | null } | null>(null);
  const [success, setSuccess] = useState("");
  const [actionError, setActionError] = useState<{ id: string; message: string } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const actionInFlight = useRef(false);
  const [review, setReview] = useState<
    (PropertySummary & { area_sqft: string | number; bedrooms: number | null; listing_consent: boolean }) | null
  >(null);

  async function listingAction(property: PropertySummary, action: "review" | "submit" | "approve") {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setActionBusy(true);
    onBusyChange(true);
    setActionError(null);
    setSuccess("");
    try {
      const path = `/staff/properties/${property.id}`;
      if (action === "review") {
        const details = await readResponse(await authFetch(path));
        if (details.status !== "submitted") throw new Error("Refresh the list to see the current status.");
        setReview(details);
        return;
      }
      await readResponse(await authFetch(`${path}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: property.version }),
      }));
      setReview(null);
      setSuccess(action === "submit" ? "Submitted for admin review." : "Published to LEIA search.");
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError({ id: property.id, message: errorMessage(error) });
    } finally {
      actionInFlight.current = false;
      setActionBusy(false);
      onBusyChange(false);
    }
  }

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setListError("");
      try {
        const response = await authFetch("/staff/properties", {
          signal: controller.signal,
        });
        const data: unknown = await readResponse(response);
        if (!Array.isArray(data) || !data.every(isProperty))
          throw new Error("The server returned an invalid property list.");
        if (active) setProperties(data);
      } catch (error) {
        if (active) setListError(errorMessage(error));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [authFetch, refresh]);

  return (
    <View style={s.workspace}>
      <Text style={s.heading}>
        {role === "admin" ? "Property workspace" : "My properties"}
      </Text>
      <Text style={s.muted}>
        {role === "admin"
          ? "View the latest 100 property records from your team."
          : "View your latest 100 property records."}
      </Text>
      {success ? (
        <Text style={s.success} accessibilityLiveRegion="polite">
          {success}
        </Text>
      ) : null}
      {!editor ? (
        <View style={s.actions}>
          <Pressable
            accessibilityRole="button"
            style={s.button}
            disabled={actionBusy || Boolean(review)}
            onPress={() => {
              setSuccess("");
              setEditor({ id: null });
            }}
          >
            <Text style={s.buttonText}>+ Add property</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={loading || actionBusy}
            style={[s.secondary, loading && s.disabled]}
            onPress={() => {
              setReview(null);
              setActionError(null);
              setRefresh((value) => value + 1);
            }}
          >
            <Text style={s.secondaryText}>Refresh list</Text>
          </Pressable>
        </View>
      ) : (
        <PropertyDraftForm
          key={editor.id || "new"}
          authFetch={authFetch}
          propertyId={editor.id}
          onBusyChange={onBusyChange}
          onCancel={() => setEditor(null)}
          onSaved={() => {
            setSuccess(
              editor.id
                ? "Draft changes saved successfully."
                : "Property draft saved successfully.",
            );
            setEditor(null);
            setRefresh((value) => value + 1);
          }}
        />
      )}
      <Text style={s.heading}>Saved properties</Text>
      {loading ? (
        <ActivityIndicator color="#355E4B" />
      ) : listError ? (
        <Text style={s.error} accessibilityRole="alert">
          {listError}
        </Text>
      ) : properties.length === 0 ? (
        <Text style={s.muted}>
          No properties yet. Add your first property draft.
        </Text>
      ) : (
        properties.map((property) => (
          <View key={property.id} style={s.panel}>
            <Text style={s.badge}>
              {property.status.toUpperCase()} ·{" "}
              {property.availability.toUpperCase()}
            </Text>
            <Text style={s.propertyTitle}>{property.title}</Text>
            <Text style={s.price}>
              {property.price === null
                ? "Price not entered"
                : `₹${property.price.toLocaleString("en-IN")}`}
            </Text>
            <Text style={s.muted}>
              {[property.locality, property.city].filter(Boolean).join(", ") ||
                "Location not entered"}
            </Text>
            <Text style={s.muted}>
              {property.property_type.replace(/_/g, " ")}
            </Text>
            {property.status === "draft" ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Edit draft: ${property.title}`}
                disabled={Boolean(editor || review) || actionBusy}
                style={[s.secondary, Boolean(editor) && s.disabled]}
                onPress={() => {
                  setSuccess("");
                  setEditor({ id: property.id });
                }}
              >
                <Text style={s.secondaryText}>Edit draft</Text>
              </Pressable>
            ) : null}
            {property.status === "draft" ? (
              <Pressable
                accessibilityRole="button"
                disabled={Boolean(editor || review) || actionBusy}
                style={s.secondary}
                onPress={() => void listingAction(property, "submit")}
              >
                <Text style={s.secondaryText}>Submit for review</Text>
              </Pressable>
            ) : null}
            {role === "admin" && property.status === "submitted" && review?.id !== property.id ? (
              <Pressable
                accessibilityRole="button"
                disabled={Boolean(editor || review) || actionBusy}
                style={s.secondary}
                onPress={() => void listingAction(property, "review")}
              >
                <Text style={s.secondaryText}>Review listing</Text>
              </Pressable>
            ) : null}
            {actionError?.id === property.id ? (
              <Text style={s.error} accessibilityRole="alert">
                {actionError.message}
              </Text>
            ) : null}
            {review?.id === property.id ? (
              <View style={s.panel}>
                <Text style={s.propertyTitle}>Customer listing preview</Text>
                <Text style={s.muted}>{review.title}</Text>
                <Text style={s.muted}>{review.locality}, {review.city}</Text>
                <Text style={s.muted}>₹{review.price?.toLocaleString("en-IN")} · {review.area_sqft} sq ft</Text>
                <Text style={s.muted}>
                  {review.property_type.replace(/_/g, " ")}
                  {review.property_type !== "plot" ? ` · ${review.bedrooms} BHK` : ""}
                </Text>
                <Text style={s.muted}>
                  {review.listing_consent ? "Owner consent recorded" : "Owner consent missing"}
                </Text>
                <Pressable
                  accessibilityRole="button"
                  style={s.button}
                  disabled={actionBusy || !review.listing_consent}
                  onPress={() => void listingAction(review, "approve")}
                >
                  <Text style={s.buttonText}>Approve and publish</Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  style={s.secondary}
                  disabled={actionBusy}
                  onPress={() => setReview(null)}
                >
                  <Text style={s.secondaryText}>Close review</Text>
                </Pressable>
              </View>
            ) : null}
          </View>
        ))
      )}
    </View>
  );
}

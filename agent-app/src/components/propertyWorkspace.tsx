import { useEffect, useState } from "react";
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
  apiUrl: string;
  accessToken: string;
  role: "agent" | "admin";
  onBusyChange: (busy: boolean) => void;
};

export default function PropertyWorkspace({
  apiUrl,
  accessToken,
  role,
  onBusyChange,
}: Props) {
  const [properties, setProperties] = useState<PropertySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [editor, setEditor] = useState<{ id: string | null } | null>(null);
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setListError("");
      try {
        const response = await fetch(`${apiUrl}/staff/properties`, {
          signal: controller.signal,
          headers: { Authorization: `Bearer ${accessToken}` },
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
  }, [apiUrl, accessToken, refresh]);

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
            onPress={() => {
              setSuccess("");
              setEditor({ id: null });
            }}
          >
            <Text style={s.buttonText}>+ Add property</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={loading}
            style={[s.secondary, loading && s.disabled]}
            onPress={() => setRefresh((value) => value + 1)}
          >
            <Text style={s.secondaryText}>Refresh list</Text>
          </Pressable>
        </View>
      ) : (
        <PropertyDraftForm
          key={editor.id || "new"}
          apiUrl={apiUrl}
          accessToken={accessToken}
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
                disabled={Boolean(editor)}
                style={[s.secondary, Boolean(editor) && s.disabled]}
                onPress={() => {
                  setSuccess("");
                  setEditor({ id: property.id });
                }}
              >
                <Text style={s.secondaryText}>Edit draft</Text>
              </Pressable>
            ) : null}
          </View>
        ))
      )}
    </View>
  );
}

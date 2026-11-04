import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { styles as s } from "../styles/property.styles";
type Props = {
  apiUrl: string;
  accessToken: string;
  role: "agent" | "admin";
};

type PropertySummary = {
  id: string;
  title: string;
  property_type: string;
  price: number | null;
  locality: string | null;
  city: string | null;
  status: string;
  availability: string;
};

const propertyTypes = [
  { value: "flat", label: "Flat" },
  { value: "independent_house", label: "Independent house" },
  { value: "plot", label: "Plot" },
] as const;

type PropertyType = (typeof propertyTypes)[number]["value"];

const fields: {
  key: string;
  label: string;
  maxLength: number;
  multiline?: boolean;
}[] = [
  { key: "title", label: "Property title *", maxLength: 200 },
  {
    key: "description",
    label: "Description",
    maxLength: 10000,
    multiline: true,
  },
  { key: "price", label: "Price in rupees, e.g. 6500000", maxLength: 16 },
  { key: "bedrooms", label: "Bedrooms", maxLength: 2 },
  { key: "bathrooms", label: "Bathrooms", maxLength: 2 },
  { key: "area_sqft", label: "Area in square feet", maxLength: 13 },
  {
    key: "address",
    label: "Full address",
    maxLength: 2000,
    multiline: true,
  },
  { key: "locality", label: "Locality", maxLength: 150 },
  { key: "city", label: "City", maxLength: 100 },
  { key: "pincode", label: "PIN code", maxLength: 6 },
  { key: "latitude", label: "Latitude", maxLength: 24 },
  { key: "longitude", label: "Longitude", maxLength: 24 },
  { key: "owner_name", label: "Owner name — staff only", maxLength: 150 },
  { key: "owner_phone", label: "Owner phone — staff only", maxLength: 30 },
  {
    key: "agent_notes",
    label: "Agent notes — staff only",
    maxLength: 10000,
    multiline: true,
  },
];

async function readResponse(response: Response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error(
        "Your session has expired. Sign out and sign in again.",
      );
    }

    if (response.status === 403) {
      throw new Error("Your account does not have access to this action.");
    }

    if (Array.isArray(data?.detail)) {
      throw new Error(
        data.detail
          .map(
            (item: { loc?: (string | number)[]; msg?: string }) =>
              `${item.loc?.slice(1).join(".") || "Form"}: ${
                item.msg || "Invalid value"
              }`,
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

  if (data === null) {
    throw new Error("The server returned an invalid response.");
  }

  return data;
}

function errorMessage(error: unknown) {
  if (error instanceof TypeError) {
    return "Could not reach LEIA. Check your connection and backend.";
  }

  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

function isProperty(value: unknown): value is PropertySummary {
  if (!value || typeof value !== "object") return false;

  const item = value as Record<string, unknown>;

  return (
    typeof item.id === "string" &&
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

export default function PropertyWorkspace({
  apiUrl,
  accessToken,
  role,
}: Props) {
  const [properties, setProperties] = useState<PropertySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [refresh, setRefresh] = useState(0);

  const [showForm, setShowForm] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [propertyType, setPropertyType] = useState<PropertyType>("flat");
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [success, setSuccess] = useState("");
  const savingRef = useRef(false);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    async function loadProperties() {
      setLoading(true);
      setListError("");

      try {
        const response = await fetch(`${apiUrl}/staff/properties`, {
          signal: controller.signal,
          headers: { Authorization: `Bearer ${accessToken}` },
        });

        const data: unknown = await readResponse(response);

        if (!Array.isArray(data) || !data.every(isProperty)) {
          throw new Error("The server returned an invalid property list.");
        }

        if (active) setProperties(data);
      } catch (error) {
        if (active) setListError(errorMessage(error));
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadProperties();

    return () => {
      active = false;
      controller.abort();
    };
  }, [apiUrl, accessToken, refresh]);

  async function saveDraft() {
    if (savingRef.current) return;

    setFormError("");
    setSuccess("");

    const payload: Record<string, string | number | boolean> = {
      property_type: propertyType,
      listing_consent: consent,
    };

    for (const field of fields) {
      const value = (values[field.key] || "").trim();
      if (value) payload[field.key] = value;
    }

    if (!payload.title) {
      setFormError("Enter a property title.");
      return;
    }

    for (const key of ["price", "bedrooms", "bathrooms"]) {
      const value = (values[key] || "").trim();
      if (!value) continue;

      if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {
        setFormError(`${key}: enter a whole number without commas.`);
        return;
      }

      const number = Number(value);

      if (
        (key === "price" && number <= 0) ||
        (key !== "price" && number > 20)
      ) {
        setFormError(
          key === "price"
            ? "Price must be greater than zero."
            : "Bedrooms and bathrooms must be between 0 and 20.",
        );
        return;
      }

      payload[key] = number;
    }

    const area = (values.area_sqft || "").trim();

    if (
      area &&
      (!/^\d+(\.\d{1,2})?$/.test(area) ||
        Number(area) <= 0 ||
        Number(area) > 9999999999.99)
    ) {
      setFormError("Enter a positive area with up to two decimal places.");
      return;
    }

    const latitude = (values.latitude || "").trim();
    const longitude = (values.longitude || "").trim();

    if (Boolean(latitude) !== Boolean(longitude)) {
      setFormError("Enter both coordinates, or leave both empty.");
      return;
    }

    for (const [key, value, limit] of [
      ["latitude", latitude, 90],
      ["longitude", longitude, 180],
    ] as const) {
      if (!value) continue;

      const number = Number(value);

      if (!Number.isFinite(number) || Math.abs(number) > limit) {
        setFormError(`Enter a valid ${key} between -${limit} and ${limit}.`);
        return;
      }

      payload[key] = number;
    }

    if (payload.pincode && !/^\d{6}$/.test(String(payload.pincode))) {
      setFormError("PIN code must contain exactly six digits.");
      return;
    }

    savingRef.current = true;
    setSaving(true);

    try {
      const response = await fetch(`${apiUrl}/staff/properties`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      await readResponse(response);

      setValues({});
      setPropertyType("flat");
      setConsent(false);
      setShowForm(false);
      setSuccess("Property draft saved successfully.");
      setRefresh((current) => current + 1);
    } catch (error) {
      setFormError(
        `${errorMessage(error)}\nIf the connection failed while saving, refresh the property list before retrying to check whether it was saved.`,
      );
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

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

      <View style={s.actions}>
        <Pressable
          accessibilityRole="button"
          disabled={saving}
          onPress={() => setShowForm((current) => !current)}
          style={[s.button, saving && s.disabled]}
        >
          <Text style={s.buttonText}>
            {showForm ? "Hide form" : "+ Add property"}
          </Text>
        </Pressable>

        <Pressable
          accessibilityRole="button"
          disabled={loading || saving}
          onPress={() => setRefresh((current) => current + 1)}
          style={[s.secondary, (loading || saving) && s.disabled]}
        >
          <Text style={s.secondaryText}>Refresh list</Text>
        </Pressable>
      </View>

      {showForm ? (
        <View style={s.panel}>
          <Text style={s.heading}>New property draft</Text>
          <Text style={s.muted}>
            Title and property type are required. You can leave other
            details empty while collecting information.
          </Text>

          <Text style={s.label}>Property type *</Text>

          <View style={s.actions}>
            {propertyTypes.map((type) => (
              <Pressable
                key={type.value}
                accessibilityRole="button"
                accessibilityState={{
                  selected: propertyType === type.value,
                }}
                disabled={saving}
                onPress={() => setPropertyType(type.value)}
                style={[
                  s.choice,
                  propertyType === type.value && s.choiceSelected,
                ]}
              >
                <Text style={s.secondaryText}>{type.label}</Text>
              </Pressable>
            ))}
          </View>

          {fields.map((field) => (
            <View key={field.key}>
              <Text style={s.label}>{field.label}</Text>
              <TextInput
                accessibilityLabel={field.label}
                value={values[field.key] || ""}
                onChangeText={(text) =>
                  setValues((current) => ({
                    ...current,
                    [field.key]: text,
                  }))
                }
                editable={!saving}
                maxLength={field.maxLength}
                multiline={field.multiline}
                style={[s.input, field.multiline && s.multiline]}
                placeholderTextColor="#879389"
              />
            </View>
          ))}

          <Text style={s.muted}>
            Coordinates can be entered manually for now. Map pin selection
            will come later.
          </Text>

          <View style={s.consentRow}>
            <Switch
              accessibilityLabel="Owner has agreed to list this property"
              value={consent}
              onValueChange={setConsent}
              disabled={saving}
              trackColor={{ false: "#CCD5C8", true: "#355E4B" }}
            />
            <Text style={s.consentText}>
              Owner has agreed to list this property.
            </Text>
          </View>

          {formError ? (
            <Text style={s.error} accessibilityRole="alert">
              {formError}
            </Text>
          ) : null}

          <Pressable
            accessibilityRole="button"
            disabled={saving}
            onPress={saveDraft}
            style={[s.button, saving && s.disabled]}
          >
            {saving ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={s.buttonText}>Save draft</Text>
            )}
          </Pressable>

          <Text style={s.muted}>
            Saving a draft does not publish it to customers.
          </Text>
        </View>
      ) : null}

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
              {[property.locality, property.city]
                .filter(Boolean)
                .join(", ") || "Location not entered"}
            </Text>

            <Text style={s.muted}>
              {property.property_type.replace(/_/g, " ")}
            </Text>
          </View>
        ))
      )}
    </View>
  );
}
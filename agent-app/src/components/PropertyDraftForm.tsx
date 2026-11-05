import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { errorMessage, isProperty, readResponse } from "../lib/propertyApi";
import {
  buildPayload,
  fields,
  initialValues,
  sections,
} from "../lib/propertyFields";
import { styles as s } from "../styles/property.styles";

type Props = {
  apiUrl: string;
  accessToken: string;
  propertyId: string | null;
  onSaved: () => void;
  onCancel: () => void;
  onBusyChange: (busy: boolean) => void;
};

export default function PropertyDraftForm({
  apiUrl,
  accessToken,
  propertyId,
  onSaved,
  onCancel,
  onBusyChange,
}: Props) {
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [consent, setConsent] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(Boolean(propertyId));
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
  const savingRef = useRef(false);

  useEffect(() => {
    if (!propertyId) return;
    let active = true;
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const response = await fetch(
          `${apiUrl}/staff/properties/${propertyId}`,
          {
            signal: controller.signal,
            headers: { Authorization: `Bearer ${accessToken}` },
          },
        );
        const raw = await readResponse(response);
        const data: Record<string, unknown> = raw;
        if (
          !isProperty(raw) ||
          data.status !== "draft" ||
          typeof data.version !== "number" ||
          !Number.isInteger(data.version) ||
          data.version < 1 ||
          typeof data.listing_consent !== "boolean"
        ) {
          throw new Error(
            "This record is not an editable draft. Refresh the list.",
          );
        }
        const next = initialValues();
        for (const field of fields) {
          const value = data[field.key];
          if (
            value === undefined ||
            (value !== null &&
              typeof value !== "string" &&
              typeof value !== "number") ||
            (field.choices &&
              !field.choices.some((choice) => choice[0] === value))
          ) {
            throw new Error("The server returned incomplete draft details.");
          }
          next[field.key] = value === null ? "" : String(value);
        }
        if (active) {
          setValues(next);
          setConsent(data.listing_consent);
          setVersion(data.version);
        }
      } catch (caught) {
        if (active) setLoadError(errorMessage(caught));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [apiUrl, accessToken, propertyId, retry]);

  async function saveDraft() {
    if (
      savingRef.current ||
      loading ||
      loadError ||
      (propertyId && version === null)
    )
      return;
    setError("");
    let payload;
    try {
      payload = {
        ...buildPayload(values, consent),
        ...(propertyId ? { version } : {}),
      };
    } catch (caught) {
      setError(errorMessage(caught));
      return;
    }
    savingRef.current = true;
    setSaving(true);
    onBusyChange(true);
    try {
      const response = await fetch(
        `${apiUrl}/staff/properties${propertyId ? `/${propertyId}` : ""}`,
        {
          method: propertyId ? "PUT" : "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        },
      );
      await readResponse(response);
      onSaved();
    } catch (caught) {
      setError(
        `${errorMessage(caught)}\nYour entries remain in this form. If the connection failed during saving, check the saved record before retrying.`,
      );
    } finally {
      savingRef.current = false;
      setSaving(false);
      onBusyChange(false);
    }
  }

  return (
    <View style={s.panel}>
      <Text style={s.heading}>
        {propertyId ? "Edit property draft" : "New property draft"}
      </Text>
      <Text style={s.muted}>
        Title and property type are required. Leave unconfirmed details unknown.
        Blank counts mean unknown; zero means none.
      </Text>
      {loading ? (
        <ActivityIndicator color="#355E4B" />
      ) : loadError ? (
        <>
          <Text style={s.error} accessibilityRole="alert">
            {loadError}
          </Text>
          <Pressable
            accessibilityRole="button"
            style={s.secondary}
            onPress={() => setRetry((value) => value + 1)}
          >
            <Text style={s.secondaryText}>Retry loading draft</Text>
          </Pressable>
        </>
      ) : (
        <>
          {sections.map((section) => (
            <View key={section.title} style={s.formSection}>
              <Text style={s.propertyTitle}>{section.title}</Text>
              {section.fields.map((field) => (
                <View key={field.key}>
                  <Text style={s.label}>{field.label}</Text>
                  {field.choices ? (
                    <View style={s.actions}>
                      {field.choices.map(([value, label]) => (
                        <Pressable
                          key={value}
                          accessibilityRole="radio"
                          accessibilityLabel={`${field.label}: ${label}`}
                          accessibilityState={{
                            checked: values[field.key] === value,
                            disabled: saving,
                          }}
                          disabled={saving}
                          style={[
                            s.choice,
                            values[field.key] === value && s.choiceSelected,
                          ]}
                          onPress={() => {
                            setValues((current) => ({
                              ...current,
                              [field.key]: value,
                            }));
                            setDirty(true);
                          }}
                        >
                          <Text style={s.secondaryText}>{label}</Text>
                        </Pressable>
                      ))}
                    </View>
                  ) : (
                    <TextInput
                      accessibilityLabel={field.label}
                      value={values[field.key] || ""}
                      onChangeText={(text) => {
                        setValues((current) => ({
                          ...current,
                          [field.key]: text,
                        }));
                        setDirty(true);
                      }}
                      editable={!saving}
                      maxLength={field.maxLength ?? 30}
                      multiline={field.multiline}
                      keyboardType={
                        field.kind === "integer"
                          ? "number-pad"
                          : field.kind === "decimal"
                            ? "decimal-pad"
                            : "default"
                      }
                      style={[s.input, field.multiline && s.multiline]}
                      placeholderTextColor="#879389"
                    />
                  )}
                </View>
              ))}
            </View>
          ))}
          <View style={s.consentRow}>
            <Switch
              accessibilityLabel="Owner has agreed to list this property"
              value={consent}
              onValueChange={(value) => {
                setConsent(value);
                setDirty(true);
              }}
              disabled={saving}
              trackColor={{ false: "#CCD5C8", true: "#355E4B" }}
            />
            <Text style={s.consentText}>
              Owner has agreed to list this property.
            </Text>
          </View>
          {error ? (
            <Text style={s.error} accessibilityRole="alert">
              {error}
            </Text>
          ) : null}
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: saving }}
            disabled={saving}
            onPress={saveDraft}
            style={[s.button, saving && s.disabled]}
          >
            {saving ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={s.buttonText}>
                {propertyId ? "Save changes" : "Save draft"}
              </Text>
            )}
          </Pressable>
          <Text style={s.muted}>
            Saving does not publish this property. Photos, 360° tours and map
            pin selection will be added later.
          </Text>
        </>
      )}
      {confirmClose ? (
        <View style={s.formSection}>
          <Text style={s.error}>Discard unsaved changes?</Text>
          <View style={s.actions}>
            <Pressable
              accessibilityRole="button"
              style={s.secondary}
              onPress={() => setConfirmClose(false)}
            >
              <Text style={s.secondaryText}>Keep editing</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              style={s.secondary}
              onPress={onCancel}
            >
              <Text style={s.secondaryText}>Discard changes</Text>
            </Pressable>
          </View>
        </View>
      ) : (
        <Pressable
          accessibilityRole="button"
          disabled={saving}
          style={[s.secondary, saving && s.disabled]}
          onPress={() => (dirty ? setConfirmClose(true) : onCancel())}
        >
          <Text style={s.secondaryText}>Close form</Text>
        </Pressable>
      )}
    </View>
  );
}

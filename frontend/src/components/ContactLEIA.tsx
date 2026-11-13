import { Link } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

export default function ContactLEIA() {
  return (
    <View style={styles.container}>
      <Text>For more details or to arrange a property visit, please call LEIA.</Text>
      <Link href="tel:+918374952855" accessibilityLabel="Call LEIA at +91 8374952855" style={styles.link}>
        Call +91 8374952855
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: 12, gap: 8 },
  link: { color: "#244D38", textDecorationLine: "underline", paddingVertical: 8 },
});

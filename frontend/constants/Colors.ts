// Sky-blue theme colors
export const skyBlueTheme = {
  skyBlue: '#87CEEB',
  darkSkyBlue: '#4682B4',
  accent: '#1E90FF',
  background: '#F0F8FF',
  danger: '#FF6347',
  success: '#32CD32',
  warning: '#FFA500',
  execution: '#9C27B0',
  system: '#607D8B',
  text: '#1C1C1E',
  textLight: '#FFFFFF',
  cardBackground: '#FFFFFF',
  border: '#E0E0E0',
};

const tintColorLight = skyBlueTheme.accent;
const tintColorDark = skyBlueTheme.skyBlue;

export default {
  light: {
    text: skyBlueTheme.text,
    background: skyBlueTheme.background,
    tint: tintColorLight,
    tabIconDefault: '#ccc',
    tabIconSelected: tintColorLight,
  },
  dark: {
    text: skyBlueTheme.textLight,
    background: skyBlueTheme.darkSkyBlue,
    tint: tintColorDark,
    tabIconDefault: '#ccc',
    tabIconSelected: tintColorDark,
  },
};

// Money display for this journal. Set in frontend/.env (restart launch.bat after changing):
//   REACT_APP_CURRENCY_SYMBOL   symbol shown before P&L amounts (R for a rand account)
//   REACT_APP_USD_TO_ACCOUNT    account currency per US dollar. Gold prices are in dollars,
//                               so a stop-loss risk or a "what if" exit worked out from
//                               prices is multiplied by this to show it in your currency.
export const CUR = process.env.REACT_APP_CURRENCY_SYMBOL || 'R';
export const USD_TO_ACCOUNT = Number(process.env.REACT_APP_USD_TO_ACCOUNT) || 16.2;
//   REACT_APP_CHART_UTC_OFFSET_HOURS  hours ahead of UTC for chart times (2 = South African time)
export const CHART_UTC_OFFSET_HOURS = Number(process.env.REACT_APP_CHART_UTC_OFFSET_HOURS ?? 2);

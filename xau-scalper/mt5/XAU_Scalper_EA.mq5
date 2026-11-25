//+------------------------------------------------------------------+
//| XAU_Scalper_EA.mq5                                               |
//| Automatic version of the xau-scalper skill for XAUUSD on M1.     |
//| Model: liquidity sweep -> close back inside -> displacement.     |
//| Same clock, filters, exits, sizing and risk gate as the skill.   |
//| SAFETY: refuses to trade a real account unless AllowLiveAccount. |
//| Not financial advice. No setup here has a proven edge.          |
//+------------------------------------------------------------------+
#property copyright "xau-scalper"
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- safety
input bool   AllowLiveAccount        = false;   // must be true to trade a REAL account
input long   MagicNumber             = 420777;
input int    MaxOpenPositions        = 1;

//--- account and risk (xau_config.json)
input double AccountSize             = 1000;    // 0 = use the live balance
input double RiskPerTradePct         = 2.0;
input double DailyLossLimitPct       = 5.0;
input int    MaxTradesPerDay         = 10;
input int    StopAfterConsecutiveLoss= 3;
input double MinRR                   = 1.5;
input double MaxSpread               = 0.40;    // price units ($)
input double TypicalSpread           = 0.20;
input int    LocalUtcOffsetHours     = 4;       // Dubai = UTC+4 (no DST): used for "today"

//--- sessions, New York wall time (follows US DST automatically)
input string AsiaKZ                  = "20:00-00:00";
input string LondonKZ                = "02:00-05:00";
input string NyKZ                    = "07:00-10:00";
input string NyOpenBan               = "09:30-10:00"; // UNVERIFIED: set your rule 7 window
input string RolloverBlock           = "16:45-18:15";
input string FridayBlockFrom         = "15:00";
input string SundayOpenBlockUntil    = "19:00";
input bool   AllowOutsideKillzones   = false;

//--- strategy
input double SweepMinPierce          = 0.10;
input int    ReclaimMaxBars          = 3;
input int    DisplacementAvgBars     = 20;
input double DisplacementBodyMult    = 1.5;
input int    DisplacementMaxBars     = 5;
input int    EqSwingBars             = 3;
input int    EqWindowBars            = 240;
input double EqTolerance             = 0.30;
input bool   UseFvgEntry             = true;
input int    LimitFillMaxBars        = 15;      // cancel an unfilled limit after N minutes
input int    WarmupDays              = 3;

//--- exits
input double StructureBuffer         = 1.50;
input bool   UseLiquidityTarget      = true;
input double DefaultTargetR          = 2.0;
input double BreakevenAtR            = 1.0;

//--- filters
input int    AtrBars                 = 14;
input double SpikeAtrMult            = 3.0;
input int    SpikeCooldownBars       = 5;
input int    FakeoutLookbackBars     = 5;
input double FakeoutWickRatio        = 0.6;
input double FakeoutMinRangeAtr      = 1.0;
input int    DoubleSweepLookbackBars = 15;

//+------------------------------------------------------------------+
CTrade trade;

// level indexes: 0..3 high-type (short setups), 4..7 low-type (long setups)
#define L_ASIA_H 0
#define L_LON_H  1
#define L_PDH    2
#define L_EQH    3
#define L_ASIA_L 4
#define L_LON_L  5
#define L_PDL    6
#define L_EQL    7
string LNAME[8] = {"asia_high","london_high","pdh","eqh","asia_low","london_low","pdl","eql"};

double lvl[8];   bool active[8]; bool taken[8];
bool   pend[8];  int pStart[8];  double pExt[8]; int pReclaim[8]; double pLevel[8];

long   curDay = -1;
double dayHi, dayLo, prevHi, prevLo;
bool   havePrev = false, haveDay = false;
double sHi[2], sLo[2]; bool sSeen[2], sDone[2];   // 0 asia, 1 london

double bodies[]; int bodyCount = 0, bodyPos = 0;
double winH[], winL[]; int winIdx[]; int winCount = 0;
double swH[], swL[]; int swHi[], swLi[];
MqlRates last3[3]; int last3n = 0;
int    barNo = -1;
datetime lastBarTime = 0;
bool   warm = false;

struct Setup { bool ok; int side; int level; double extreme; double entry; bool isLimit;
               bool hasTarget; double target; int tgtLevel; };

//+------------------------------------------------------------------+
int HM(string s){ return (int)StringToInteger(StringSubstr(s,0,2))*60 + (int)StringToInteger(StringSubstr(s,3,2)); }
void Win(string s, int &a, int &b){ a = HM(StringSubstr(s,0,5)); b = HM(StringSubstr(s,6,5)); }
bool InWin(int m, string w){ int a,b; Win(w,a,b); if(a==b) return false; if(a<b) return (m>=a && m<b); return (m>=a || m<b); }

// US DST: second Sunday of March 07:00 UTC to first Sunday of November 06:00 UTC
datetime NthSunday(int year, int mon, int n){
   MqlDateTime t; t.year=year; t.mon=mon; t.day=1; t.hour=0; t.min=0; t.sec=0;
   datetime d = StructToTime(t); MqlDateTime w; TimeToStruct(d,w);
   int first = (7 - w.day_of_week) % 7;
   return d + (first + 7*(n-1)) * 86400;
}
int NyOffsetHours(datetime gmt){
   MqlDateTime t; TimeToStruct(gmt,t);
   datetime s = NthSunday(t.year,3,2) + 7*3600, e = NthSunday(t.year,11,1) + 6*3600;
   return (gmt>=s && gmt<e) ? -4 : -5;
}
int ServerOffsetSec(){ int d = (int)(TimeTradeServer() - TimeGMT()); return (int)MathRound(d/1800.0)*1800; }
datetime ToNY(datetime serverTime){ datetime g = serverTime - ServerOffsetSec(); return g + NyOffsetHours(g)*3600; }
int NyMinute(datetime ny){ MqlDateTime t; TimeToStruct(ny,t); return t.hour*60+t.min; }
long SessionDay(datetime ny){ int rEndA,rEndB; Win(RolloverBlock,rEndA,rEndB); return (long)((ny + (1440 - rEndB)*60) / 86400); }

int Killzone(int m){ if(InWin(m,AsiaKZ)) return 0; if(InWin(m,LondonKZ)) return 1; if(InWin(m,NyKZ)) return 2; return -1; }

string ClockBlock(datetime nowServer){
   datetime ny = ToNY(nowServer); MqlDateTime t; TimeToStruct(ny,t); int m = t.hour*60+t.min;
   if(t.day_of_week==6 || (t.day_of_week==5 && m>=HM(FridayBlockFrom)) || (t.day_of_week==0 && m<HM(SundayOpenBlockUntil))) return "weekend";
   if(InWin(m,RolloverBlock)) return "rollover";
   if(StringLen(NyOpenBan)>=11 && InWin(m,NyOpenBan)) return "NY-open ban";
   if(Killzone(m)<0 && !AllowOutsideKillzones) return "dead hours";
   return "";
}

//+------------------------------------------------------------------ detector
void NewDay(long d){
   if(haveDay){ prevHi=dayHi; prevLo=dayLo; havePrev=true; }
   curDay=d; haveDay=false;
   for(int i=0;i<8;i++){ active[i]=false; taken[i]=false; pend[i]=false; }
   for(int k=0;k<2;k++){ sSeen[k]=false; sDone[k]=false; }
   if(havePrev){ lvl[L_PDH]=prevHi; active[L_PDH]=true; lvl[L_PDL]=prevLo; active[L_PDL]=true; }
}
double AvgBody(){ if(bodyCount<DisplacementAvgBars) return -1; double s=0; for(int i=0;i<DisplacementAvgBars;i++) s+=bodies[i]; return s/DisplacementAvgBars; }

void NearestTarget(double price, int side, bool &has, double &tgt, int &which){
   has=false; int a = side>0 ? 0 : 4;
   for(int i=a;i<a+4;i++){
      if(!active[i] || taken[i]) continue;
      if((side>0 && lvl[i]>price) || (side<0 && lvl[i]<price)){
         if(!has || MathAbs(lvl[i]-price)<MathAbs(tgt-price)){ has=true; tgt=lvl[i]; which=i; }
      }
   }
}

Setup MakeSetup(int li, const MqlRates &b){
   Setup s; s.ok=true; bool high = li<4; s.side = high ? -1 : 1; s.level=li; s.extreme=pExt[li];
   s.entry=b.close; s.isLimit=false;
   if(UseFvgEntry && last3n==3){
      MqlRates a = last3[0];
      if(high && b.high < a.low){ s.entry=(b.high+a.low)/2; s.isLimit=true; }
      if(!high && b.low > a.high){ s.entry=(a.high+b.low)/2; s.isLimit=true; }
   }
   NearestTarget(s.entry, s.side, s.hasTarget, s.target, s.tgtLevel);
   return s;
}

void AddSwing(double &px[], int &ix[], double v, int idx, int horizon, int li, bool high){
   int n = ArraySize(px), keep=0;
   for(int j=0;j<n;j++) if(ix[j]>=horizon){ px[keep]=px[j]; ix[keep]=ix[j]; keep++; }
   ArrayResize(px,keep); ArrayResize(ix,keep);
   bool match=false; double best=v;
   for(int j=0;j<keep;j++) if(MathAbs(px[j]-v)<=EqTolerance){ match=true; best = high? MathMax(best,px[j]) : MathMin(best,px[j]); }
   ArrayResize(px,keep+1); ArrayResize(ix,keep+1); px[keep]=v; ix[keep]=idx;
   if(match && !taken[li]){ lvl[li]=best; active[li]=true; }
}

Setup Step(const MqlRates &b){
   Setup found; found.ok=false;
   barNo++;
   datetime ny = ToNY(b.time); int m = NyMinute(ny);
   long d = SessionDay(ny); if(d!=curDay) NewDay(d);
   if(last3n<3){ last3[last3n++]=b; } else { last3[0]=last3[1]; last3[1]=last3[2]; last3[2]=b; }
   double body = MathAbs(b.close-b.open);
   // sweeps
   for(int i=0;i<8;i++){
      if(!active[i] || taken[i]) continue;
      bool high = i<4;
      if((high && b.high>=lvl[i]+SweepMinPierce) || (!high && b.low<=lvl[i]-SweepMinPierce)){
         taken[i]=true; pend[i]=true; pStart[i]=barNo; pExt[i]= high? b.high : b.low; pReclaim[i]=-1; pLevel[i]=lvl[i];
      }
   }
   double avg = AvgBody();
   for(int i=0;i<8;i++){
      if(!pend[i]) continue;
      bool high = i<4;
      pExt[i] = high ? MathMax(pExt[i],b.high) : MathMin(pExt[i],b.low);
      if(pReclaim[i]<0){
         if((high && b.close<pLevel[i]) || (!high && b.close>pLevel[i])) pReclaim[i]=barNo;
         else if(barNo-pStart[i]>=ReclaimMaxBars){ pend[i]=false; continue; }
      }
      if(pReclaim[i]>=0){
         bool disp = avg>0 && body>=DisplacementBodyMult*avg && ((high && b.close<b.open) || (!high && b.close>b.open));
         if(disp){ Setup s = MakeSetup(i,b); pend[i]=false; if(!found.ok) found=s; }
         else if(barNo-pReclaim[i]>=DisplacementMaxBars) pend[i]=false;
      }
   }
   // session ranges become liquidity when their killzone ends
   int kz = Killzone(m);
   for(int k=0;k<2;k++){
      if(sDone[k]) continue;
      if(kz==k){ if(!sSeen[k]){ sHi[k]=b.high; sLo[k]=b.low; sSeen[k]=true; } else { sHi[k]=MathMax(sHi[k],b.high); sLo[k]=MathMin(sLo[k],b.low); } }
      else if(sSeen[k]){ sDone[k]=true; int h = k==0? L_ASIA_H:L_LON_H, l = k==0? L_ASIA_L:L_LON_L;
         lvl[h]=sHi[k]; active[h]=true; lvl[l]=sLo[k]; active[l]=true; }
   }
   // equal highs / lows from confirmed fractal swings
   int W = 2*EqSwingBars+1;
   if(winCount<W){ winH[winCount]=b.high; winL[winCount]=b.low; winIdx[winCount]=barNo; winCount++; }
   else { for(int j=0;j<W-1;j++){ winH[j]=winH[j+1]; winL[j]=winL[j+1]; winIdx[j]=winIdx[j+1]; } winH[W-1]=b.high; winL[W-1]=b.low; winIdx[W-1]=barNo; }
   if(winCount==W){
      int c = EqSwingBars; bool isH=true, isL=true;
      for(int j=0;j<W;j++){ if(j==c) continue; if(!(winH[c]>winH[j])) isH=false; if(!(winL[c]<winL[j])) isL=false; }
      int horizon = barNo - EqWindowBars;
      if(isH) AddSwing(swH, swHi, winH[c], winIdx[c], horizon, L_EQH, true);
      if(isL) AddSwing(swL, swLi, winL[c], winIdx[c], horizon, L_EQL, false);
   }
   if(!haveDay){ dayHi=b.high; dayLo=b.low; haveDay=true; } else { dayHi=MathMax(dayHi,b.high); dayLo=MathMin(dayLo,b.low); }
   bodies[bodyPos]=body; bodyPos=(bodyPos+1)%DisplacementAvgBars; if(bodyCount<DisplacementAvgBars) bodyCount++;
   return found;
}

//+------------------------------------------------------------------ filters
string Filters(int side, double spread){
   if(spread>MaxSpread) return StringFormat("spread %.2f > %.2f", spread, MaxSpread);
   int need = MathMax(2*DoubleSweepLookbackBars, AtrBars+SpikeCooldownBars+1) + 1;
   MqlRates r[]; ArraySetAsSeries(r,false);
   if(CopyRates(_Symbol,PERIOD_M1,1,need,r)<need) return "not enough bars";
   int n = need; int end = n - SpikeCooldownBars; double atr=0;
   for(int i=end-AtrBars;i<end;i++) atr += MathMax(r[i].high-r[i].low, MathMax(MathAbs(r[i].high-r[i-1].close), MathAbs(r[i].low-r[i-1].close)));
   atr /= AtrBars;
   for(int i=n-SpikeCooldownBars;i<n;i++) if(r[i].high-r[i].low > SpikeAtrMult*atr) return "spike";
   for(int i=n-FakeoutLookbackBars;i<n;i++){
      double rg = r[i].high-r[i].low; if(rg<=0 || rg<FakeoutMinRangeAtr*atr) continue;
      double against = side>0 ? r[i].high-MathMax(r[i].open,r[i].close) : MathMin(r[i].open,r[i].close)-r[i].low;
      if(against/rg>=FakeoutWickRatio) return "stop-hunt wick against the trade";
   }
   int L = DoubleSweepLookbackBars; double ph=-1e18,pl=1e18,lh=-1e18,ll=1e18;
   for(int i=n-2*L;i<n-L;i++){ ph=MathMax(ph,r[i].high); pl=MathMin(pl,r[i].low); }
   for(int i=n-L;i<n;i++){ lh=MathMax(lh,r[i].high); ll=MathMin(ll,r[i].low); }
   if(lh>ph && ll<pl) return "both sides hunted";
   return "";
}

//+------------------------------------------------------------------ day state
datetime LocalDayStartServer(){
   datetime g = TimeGMT(); datetime loc = g + LocalUtcOffsetHours*3600;
   datetime locMidnight = loc - (loc % 86400);
   return locMidnight - LocalUtcOffsetHours*3600 + ServerOffsetSec();
}
void DayState(int &trades, double &realised, int &streak){
   trades=0; realised=0; streak=0;
   datetime from = LocalDayStartServer();
   if(!HistorySelect(from, TimeTradeServer()+60)) return;
   int n = HistoryDealsTotal();
   for(int i=0;i<n;i++){
      ulong t = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(t,DEAL_MAGIC)!=MagicNumber || HistoryDealGetString(t,DEAL_SYMBOL)!=_Symbol) continue;
      long entry = HistoryDealGetInteger(t,DEAL_ENTRY);
      if(entry==DEAL_ENTRY_IN) trades++;
      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY){
         double p = HistoryDealGetDouble(t,DEAL_PROFIT)+HistoryDealGetDouble(t,DEAL_COMMISSION)+HistoryDealGetDouble(t,DEAL_SWAP);
         realised += p; streak = p<0 ? streak+1 : 0;
      }
   }
   // pending orders placed today count as trades too
   for(int i=OrdersTotal()-1;i>=0;i--){ ulong o=OrderGetTicket(i); if(OrderGetInteger(ORDER_MAGIC)==MagicNumber && OrderGetString(ORDER_SYMBOL)==_Symbol) trades++; }
}
int MyPositions(){ int c=0; for(int i=PositionsTotal()-1;i>=0;i--){ ulong t=PositionGetTicket(i); if(PositionGetInteger(POSITION_MAGIC)==MagicNumber && PositionGetString(POSITION_SYMBOL)==_Symbol) c++; } return c; }
int MyOrders(){ int c=0; for(int i=OrdersTotal()-1;i>=0;i--){ ulong t=OrderGetTicket(i); if(OrderGetInteger(ORDER_MAGIC)==MagicNumber && OrderGetString(ORDER_SYMBOL)==_Symbol) c++; } return c; }

//+------------------------------------------------------------------ sizing (never rounds up)
double LossPerLot(double dist){
   double ts = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE), tv = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(ts<=0 || tv<=0) return -1; return dist/ts*tv;
}
double SizeLot(double dist, double riskMoney, string &why){
   double lpl = LossPerLot(dist); if(lpl<=0){ why="no tick value"; return 0; }
   double step = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP), mn = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN), mx = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double lot = MathFloor(riskMoney/lpl/step + 1e-9)*step;
   if(lot*lpl > riskMoney + 1e-6) lot -= step;   // guard: never above the risk
   lot = MathMin(lot, mx);
   if(lot < mn - 1e-9){ why=StringFormat("stop too wide: min lot would risk %.2f > %.2f", mn*lpl, riskMoney); return 0; }
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------ the gate + order
void XLog(string s){
   Print("[xau] ", s);
   int h = FileOpen("xau_scalper_log.csv", FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(h!=INVALID_HANDLE){ FileSeek(h,0,SEEK_END); FileWriteString(h, TimeToString(TimeTradeServer(),TIME_DATE|TIME_MINUTES)+","+s+"\n"); FileClose(h); }
}

void Evaluate(Setup &s){
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID), ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK), spread=ask-bid;
   string tag = LNAME[s.level] + (s.side>0? "_sweep_long":"_sweep_short");
   string blocks="";
   string clk = ClockBlock(TimeTradeServer()); if(clk!="") blocks += "session:"+clk+" ";
   string f = Filters(s.side, spread);     if(f!="") blocks += "filters:"+f+" ";
   double buf = StructureBuffer + spread;
   double stop = s.side>0 ? s.extreme-buf : s.extreme+buf;
   double entry = s.entry; bool limit = s.isLimit;
   if(limit && ((s.side>0 && entry>=ask) || (s.side<0 && entry<=bid))) { limit=false; }
   if(!limit) entry = s.side>0 ? ask : bid;
   double risk = s.side>0 ? entry-stop : stop-entry;
   if(risk<=0){ XLog(tag+",SKIP,stop not beyond entry"); return; }
   double tp = (UseLiquidityTarget && s.hasTarget) ? s.target : entry + s.side*DefaultTargetR*risk;
   double rr = s.side*(tp-entry)/risk;
   if(rr < MinRR) blocks += StringFormat("min_rr:%.2f<%.2f ", rr, MinRR);
   double acct = AccountSize>0 ? AccountSize : AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = acct*RiskPerTradePct/100.0, limitMoney = acct*DailyLossLimitPct/100.0;
   string why=""; double lot = SizeLot(MathAbs(entry-stop), riskMoney, why);
   if(lot<=0) blocks += "risk_per_trade:"+why+" ";
   int trades; double realised; int streak; DayState(trades, realised, streak);
   if(trades>=MaxTradesPerDay) blocks += "max_trades ";
   double thisRisk = lot>0 ? lot*LossPerLot(MathAbs(entry-stop)) : 0;
   if(realised<=-limitMoney || -realised+thisRisk > limitMoney+1e-6) blocks += StringFormat("daily_loss:%.2f ", realised);
   if(streak>=StopAfterConsecutiveLoss) blocks += "consecutive_losses ";
   if(MyPositions()+MyOrders()>=MaxOpenPositions) blocks += "already_in_a_trade ";
   if(blocks!=""){ XLog(StringFormat("%s,BLOCK,%s", tag, blocks)); return; }

   int dg=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   entry=NormalizeDouble(entry,dg); stop=NormalizeDouble(stop,dg); tp=NormalizeDouble(tp,dg);
   string cm = StringFormat("%s R%.1f", tag, rr);
   bool ok;
   if(limit) ok = s.side>0 ? trade.BuyLimit(lot,entry,_Symbol,stop,tp,ORDER_TIME_GTC,0,cm) : trade.SellLimit(lot,entry,_Symbol,stop,tp,ORDER_TIME_GTC,0,cm);
   else      ok = s.side>0 ? trade.Buy(lot,_Symbol,0,stop,tp,cm) : trade.Sell(lot,_Symbol,0,stop,tp,cm);
   XLog(StringFormat("%s,%s,%s lot %.2f entry %.2f sl %.2f tp %.2f rr %.2f risk %.2f ret %d",
       tag, ok?"SENT":"FAILED", limit?"limit":"market", lot, entry, stop, tp, rr, thisRisk, (int)trade.ResultRetcode()));
}

//+------------------------------------------------------------------ management
void Manage(){
   // cancel stale limits, and any pending order when the clock blocks
   string clk = ClockBlock(TimeTradeServer());
   for(int i=OrdersTotal()-1;i>=0;i--){
      ulong t=OrderGetTicket(i);
      if(OrderGetInteger(ORDER_MAGIC)!=MagicNumber || OrderGetString(ORDER_SYMBOL)!=_Symbol) continue;
      datetime setup=(datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(TimeTradeServer()-setup >= LimitFillMaxBars*60 || clk!=""){ trade.OrderDelete(t); XLog("cancel pending "+(string)t); }
   }
   // breakeven at BreakevenAtR
   for(int i=PositionsTotal()-1;i>=0;i--){
      ulong t=PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC)!=MagicNumber || PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      double op=PositionGetDouble(POSITION_PRICE_OPEN), sl=PositionGetDouble(POSITION_SL), tp=PositionGetDouble(POSITION_TP);
      bool lng = PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
      double risk = lng ? op-sl : sl-op; if(risk<=0) continue;           // already at/after breakeven
      double px = lng ? SymbolInfoDouble(_Symbol,SYMBOL_BID) : SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      if((lng && px-op>=BreakevenAtR*risk) || (!lng && op-px>=BreakevenAtR*risk)){
         if(trade.PositionModify(t, NormalizeDouble(op,_Digits), tp)) XLog("breakeven "+(string)t);
      }
   }
}

//+------------------------------------------------------------------ events
int OnInit(){
   if(_Symbol!="XAUUSD" && StringFind(_Symbol,"XAU")<0) Print("[xau] warning: built for XAUUSD, attached to ", _Symbol);
   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
   if(!demo && !AllowLiveAccount && !MQLInfoInteger(MQL_TESTER)){
      Alert("XAU_Scalper_EA: REAL account. Set AllowLiveAccount=true only after demo testing. EA stopped.");
      return INIT_FAILED;
   }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   ArrayResize(bodies,DisplacementAvgBars); ArrayResize(winH,2*EqSwingBars+1); ArrayResize(winL,2*EqSwingBars+1); ArrayResize(winIdx,2*EqSwingBars+1);
   // warm up the levels on recent history without trading
   int n = WarmupDays*1440;
   MqlRates r[]; ArraySetAsSeries(r,false);
   int got = CopyRates(_Symbol,PERIOD_M1,1,n,r);
   for(int i=0;i<got;i++){ Step(r[i]); lastBarTime=r[i].time; }
   warm=true;
   Print("[xau] ready: ", got, " warmup bars, demo=", demo);
   return INIT_SUCCEEDED;
}

void OnTick(){
   Manage();
   MqlRates r[]; ArraySetAsSeries(r,false);
   if(CopyRates(_Symbol,PERIOD_M1,1,1,r)!=1) return;      // last CLOSED bar
   if(r[0].time==lastBarTime) return;
   // feed any bars we missed, oldest first
   MqlRates gap[]; ArraySetAsSeries(gap,false);
   int k = Bars(_Symbol,PERIOD_M1,lastBarTime,r[0].time);
   if(lastBarTime>0 && k>2){ int c=CopyRates(_Symbol,PERIOD_M1,lastBarTime+60,r[0].time-60,gap); for(int i=0;i<c;i++) if(gap[i].time>lastBarTime) Step(gap[i]); }
   lastBarTime=r[0].time;
   Setup s = Step(r[0]);
   if(warm && s.ok) Evaluate(s);
}
//+------------------------------------------------------------------+

# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generated wire descriptors from NVIDIA OpenShell v0.1.2 (Apache-2.0).
Regenerate with tools/generate_approval_schema.py. Field definitions are unmodified;
unrelated RPCs/messages, comments and custom authorization options are omitted.
See docs/native-api-v0.1.2.md and docs/third-party/OpenShell-LICENSE.txt.
"""
import base64
import zlib
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

SOURCE_HASHES = {'openshell.proto': '2e31d0faca652b91729f88bd4053447e5d162e74de7dbacdcc06327ed82e5b95', 'sandbox.proto': 'ed3d2650c8d1c4cbb653259c6055adba2dba8a51498ab6a1b2cfc339997ab2b9', 'datamodel.proto': 'e50bd875a760f54b80269c65ee47984cae424fb623b7f59551ba62550c23e70b', 'options.proto': '10c8a99b505047f759951285fdae6068afd267ef672d00a65385a91ee8d027ee'}
SCHEMA_SHA256 = 'd72ee3724893197948aebfb2d8fbfc5621e1b912d9710028021b587a185c9353'
_DATA = (
    'c%0okOK&4*a!WlZvPept9*xA|Xf!jNvDcPu*&grOUO$(js1er`NjIq(8^Na0B3lwYEjHcV)Ql1!KrRa(0%UI+B!@lafdDxqm*fy+'
    '4?zyOY>)ss1Ucmw<eW=3Ro|mu?51YyH5SN4;#aS)zE^!!^;N?YxOHM#C#LcG*tT5jXmb3zJ3KQSS3etXGmx^v3AQCc<$cKZB#lyn'
    'CCBJnqk&UO-A=tUuga#f3X3CsWI3fdgfFVRag=6a-hzKf!^%KM5!S#kMNezi*cdsdhG}m5sPi|=m#J9!X$r19vh2ssSnnHM!!-J?'
    'Wh<MIeIkKOx$ORTR3D_YU|Ba!%>!^vbN64^j@M(mYMNdz4$3O%LBLK9vJUHeXpJf>PzaG%{%HZAee?yQ4-8jVmSJu<;99BpOHp89'
    'q@Ni~g{;bcD<49>ZyUO63^W>X>4d3Q-i|EDwznE;PS6uX*@LvH9~q`oTDU!rjN3`$^3~dIGKTudwJ%h`t-K6H+i<Lj-8Zyz!*+0Z'
    'rA1U_L0ysXha9XNLQWrzELW#Fa7t;`?_1B%Z`J4God@M88^M)aVlxz2$B&_a0u3VN^JHE+PkG5epz;qO7Z|rvg!#wDg;*Q<Nn80`'
    'Hz#s+c;mgfcT?q$pcqyCdH8>{keM$o;NMI!s}|&9>#la@sMkV+VtOOkJ3tfIj2?@@#AbRV9o+XIvnxox_PPD91=xu8>$vu$?<V#7'
    'M=3~m8HMsbq>qP&$@-+7ad&EoIN|mlqY~SYvy?xDTmT$1%dZA!Id^|;O87&<24=tVH*>JafT$UaCTI+}01~<!vw>Pj!syA6jATQ('
    '3;EIH?8vY+zyB1H<T%tL!g(5wP6W}sFi1HL<wUmtjx0;6v4A8Q>WW~T-~%QFr$~@2vZ1ag%%c#Gzr0bJLr3za6OKs}NxV^-vayqf'
    'g~!9ufXu%~p=?8%36m?DH0gqB>jq@KNkkh6t;1$hdsuBA){Ecq_Snzgg@U7x21nLYv8h&l3nCGCFU;JC<x7r7IePt6{$dddT`s+2'
    'nZy1C9TI8qXiF4S*+kn4$3xR_E*#f56C3Wu?KmxQFH1TuRSj?|KZJ~lq^8w>%yvZX{=Il9O+h7OcwBf%ETQ^%!#Hfe(4mSI(BVXa'
    'U-Bq@D2@!5jHxyz)DV|)kuBxB@p3{lwB6#o9l^>^?8+z%RZO@lJ2@K;2Bz^u$D2c35AQ!mKL<WH?@Ad>A<B<oHBPM<i}7LDVthAV'
    'lc2B*bz%lhNZEz;1l?l#tA+b}ZDxPIyqYyS9-agZ*Apm4x0Ui;SQ(D`=44<nmu4`uS=SkLS%4pLsDft@FPed7jm!(wWFEE45TK21'
    '<t6|_KCy=`87aat@<#-yyba6Yov7S{Lf<+Y<H<T2nnPEPHw>(N3Wd-HMCUALPez*VXcNb<1ydG5Rp$@^%5BIC{E2N%#^UA#M(}_j'
    'mA^u#Z)#~gKPnaQqiT>fMuV|69Jvk~aM;J2p8C3=R((G7GBQWQk#5rgnHRU$EEzjQj0aURReqI<*(!W#W3}D{@DGa|zYkGfhjgED'
    '3&W9g38;chxegh0CJfuQY-TF5>XJ38Qxjd`zzp#_<&F5!`KrrFlKysZve1XH>;bN8n)?u}QRzTAnVpQS*r@C-LG477Fve4k;oDU<'
    'aXXg8l-6uxV2s?MZsNob@IqC}kinTa9}c|j<+UsSnS+(cTGJYwT8=B%fPNEfY}qcG{esGV3%Rb<w@iMCt6p1q8|Gb;8{*|8OD_8D'
    'na%-TJ}NQb{O63(v4x)W84iN2!1Dc9FWI9WJ(Cj@pj08P_t~A0W};r6DXPjyJ7~BJ+%23@xetpr9w-O>ZQAS`3D?p)+#oVibYF59'
    '5?N2shaHZcQid%S2vB3p_65zMQ_3Q$pz;PDEx@(A(0G5q`n#Rf-!4+JKC=Vy%Rw*XD(gfuMtz)%f##UHb6UzV^LK@z>KxN0gz^ep'
    '8Pi4XkhlHi#9)Uy&(^Z&AwK1B<@ezFiLH-MzcjUJrUFxT=b5a@_5&`a6X}89Wa}sKSmiBPll`CRPqia!aG@PtxY+qJ+m%-Z_CSBS'
    'gSZ_+v5l*{r==CXndvwB8XHfHBL_{l;c9*FylA$;jeU_#SJ|WSkCe(Olseq94IDbUkW7sjAy=4?4M9c<O^d;RQXiZT9n046<cuxH'
    '7?c#ow<6#j0ajiECD4g8JQ?B9E?s4#y5eKH96@)WpwaLWIUi!UxXNgk1-Q$h$_pSNsQC#wc-Q!~C7>z;D&K?kFB~+Q_P8IN{53xL'
    'gjUD>(B$8T`Lq7Gw9f2k!bU#mkK1DwG^#|b{5UkwBW}Y=B_?8f=JuMhe8Gv4#&8F2@)I(_(K%zZxVVXZGN*K%&HoKXbm-Ig!f7L3'
    'lLj=vifdP9ysHvf3*gE&Y|PsBF;3D4VJ9iUs}@4rcqExqAM}p?Ed}7uFJ6>aD8|B=$)(teE>|A(Qf#%tQ9Z=82=P@ah1QeLgGTA0'
    '$0O^>NW&%4&J6d|8t}L{v!7De8Bx_VKOqwA@PHGL2)rajsNe`E%4_eef}%|+w6h5t+KgT+PoWqcGwotaHZ=EQEFFk~2kjv+Ocuy8'
    '`D8S7WzCDuxyGVZ`Dy{cx4T5fbrPM4mS=M20wxD<ZuA4saM{FFDVsRQi)2GDL~iPp%gp^Y?V1a2DmiD&=og=D^y%57UkZ+1*@g62'
    'xAilAXXTP^<$$IgMlO;UoVWbj0R8}CHvN0yO`jA5zQcqZ=x+biu!CWU2Dw1Dzy1z*imLJ<r1_1H#$b`{R@O)E{>>zLRU%S3ZRJ<<'
    'up~ggrfu|5cpJSmQ;px)9;!z6P=Y%q)%<_Ffhc|qk<fadnAVYWyXiOeu8(AFN<S<2p%99zyls;5w+VJJSDaUuPIY^P9&F|JG2nV&'
    'xFV4tf@toSC@QRY31rRb<5)x#6<nH7vU=)$L0$GFbV)#55Lfwm3i4gUb-fH<Gz}=<C1#`n=cdzuHzA7-WgwF{kN6CNIQkCcn3EF7'
    'Bj%9+7?5Z4mc(*aev*P>{rH%kEzS+0s`9Q}Y<{{c2`QSuz{FA$C!fwa7I@6&Wb>wShRR=OpftT)&KR#u%3P*ddg?-Gln{G|l18dv'
    '38wMp!f8AVSU8P#N&y2mF2F(i6e)ZL8HejCUr|0t@JA9fmqQiD*^@@DDv!(20Y#tJe^goS3jrLeJb;4Gk-xQwkS<|n`Ov$JQq7M8'
    'Ep$t-(dk0>Qpl5&vLxaNhU5a|gU<bc&u5IzeO)t5*Q*j)ADw#&KY>kWGA2ixJbXW6AzgB51>a0qLyy-_76`Xp-EfkTk!K=^J`>eo'
    'Q;u6VhXZcQkM*HBLDlG(vv@J9-r!UZJ)%8MLs7P}54Vk{E^1B8`oujYN4L*q86*9O+~GWPhc`XCYKW}PsnDKY_hwdiUA=!wk-KJ$'
    't^O&C?6T^*B&d4$I-!;C!y4ZtBBQP8gR`NFt~JJ^)c3UzwknY52wx4e@mx@mb*6++JS~Jeh8Zv}aP<+I%;7+Umg^ychQK!l<vtWb'
    'TmNf&`cdN)^7QB6#*F<En^(IFg%gVBWVT9p8hHYG$)GK`Lh;^Mzc4L*AVR)*{^*H_4u|t_ZwFRnR7@{**2FEHvH@M6%E-y*H+5NX'
    '^f&`L7%Q&nXnkGl8@8*BjWcEq@@gKzHNA$wj&Vj|g+3V!jZt5EAYL0SZ;8hbxJX8yHnnT(7{^imYKd)&HHL2pbWdXO-drdys!Kxo'
    't)Hb}Ih>?YZo@{a-h0$mKhb*4u6EGgt!sy^Zl_*r>^17U#qTKB;o1~M_mf7am{KZGo<jBN>OrGb?bVBO<+)5sxfjzxr`qlI_SN>`'
    'gZ<(>QEmM}3fAJ&U&>2xPYS5F_S$N#eo$}q;xxSnw-YG^fGNG1NL4-DZS;zBTYsK{t1&4wT+BO?w_2;!yIrlL*1PpyM3K92E1pcP'
    'S9i5`tND2`71TC{=#ko>DO5K7DGpQZ9X6ZA`K=?*3*oNHO}LKOUZeFu>(<(xn8ggEyVch2PWv;|rv$5E)PrVwr`p8Xz&?VP5B1S$'
    'HygFjwQl<m=Wn`?Fgnyn7$y7gUI_oN1uMR>;BSO^A8`KW)a%}*&w;Za6E7=4DTf~{sr+N#&`tMLIGhy?mL)*AG;cu~1-KJOJU8Fi'
    '4h~>jn6NG*Q3Yw#mC1UpOy!qzup~SXjEXWCP3bfRF&<n(4TkFRD6hc+<$r`-z8+DGchW@ym5~KT!hPuINCZQ8Bra^qdWU1%I%98E'
    '%+|Wb+1Nxl%iQJezaA^>k)ZAg;tt-Fc>kguRBs5ednh$Z#!VgmT`Z>6#W6a4hPsBv(59<5>=jjhu>iTiSn1i!v`#eBI5$lBpn!lT'
    'wUs6%@Xv>~H4+JYa&Wd1CvLm$6N`G60K4)7$au4mo{F!1QF2(SM;>sLm!98IMh8l|95Q?F1C&Q_&8q{V>uYE;^Gohrbc<mRsvuPZ'
    'l<KM-0}Y1vdH%j5!+Z1@T=znFHT3hFxJ^{76P@?xEY^Wy4*%wgc@<8^6K!BHuXK%PvC*;C$J96epz~E}!)n|tDDS{La*LDsLU`I9'
    'j3==(M;w)pFrbacqd8sdTayugzA9j_$D3cJQac$)YphZE??qUNY#kcz@Jv6EJ`!&zuYzJD2O2r8Z|aT{OdA#jrbY;s_qYRjWWr_O'
    '4+kQS$}w1%fhYwMDn@&G=7x@~L+gZ#%ND1K+G6C)t}E+=Pi^{HB+fp$->0{AzC4aNfr;<3J|#}~3O$4KR4}ha1QGQw6Qbq8DY^Us'
    '6b3dr#I`0LfOE4u%5!QSnEnQ<-M;Ao+)<#YMdk^QW3~wD+PP&;_+}lRc~^*2If6Tv-Wah_LR`@!*C$%^SovqaZiwG;bXD;4Ft!GQ'
    'E5iUCYe3KN9C_?zGg*6YK*blrMiY|1(Psr@kCL50C3?f){@;w<tu;xe1~`;AfM0ncgHVoT?XXLc6Dr?>gxgW%3|0j;oHTd`N@x0T'
    'q=_1%P}G=VgStj|Q4r$Cj2LEQji*Cbqo-PSt1MDli$FDm(AICj6&bf_+>!8Jj5or=@#+h>+fg(^Z9H=WSf37}9X2?Ao?c0s+Hh#i'
    'Sk$$tkek8rtVVmGSeE<~o+sE(MOh`DhFga`yUa)wy$2SUVt3*fE<mV;*mm*&nm4gKWhiM0I#vEP1;ywdqVo}WTw=c|)h&EJzQ3hg'
    'hj4Vi9GpbHw?&KYcs#NWT{NQhL^PEehQ1Vv4*de&;qA)`c$a+^0t%0A6>lV0i%6^dE?n`mb2AdabKgb+_}^3BmFs!_ufvi!)WQe2'
    '7Q3LTq*6YGyvUtt6Wf$EOz{_0Ao0jJ4sCgl<{TeFrZ`IQJf^tvixk}SXH^7#{HP_*@WMBp;p9v%?mM2pWcx0oiWaML4$O7do7Boz'
    'DcDRJfmo=IA>$=Hr}NHJZnPj(JtlfLVrMIShZNKThsqn|@8{s=O!GpC>Yx?nLxG`hIzNU*I)O6p9&xYYrP63A{Z9W#)rg6o=c<lF'
    'wmp(DUzp06KXYC?F}0jdPA%hp<T*?J5dd#g{$v5J#jeDR0^Z0t=BCDRKNQqX-Ik&KB8?y3-z<a;*4SkU!qchI?>R0vbD{4!-iI8?'
    'lH6Zh$_<=cFn@!%unREu+$9zmhQI1aItkmplf_L?Kuq~VSc#yYGZ;gQkAKs}rRbLvc`;3_Rr#MR<hWY3)5+-Z)R#9&HyM@5PE3PG'
    'KBCZ%04(BZLP3@;v9dK`+Mc<BI4`-`V>-s&{AlW?e1P@=tX7FKwnO(qL+9IY<m0o1wg^b|qU?kXSaz6Jc}@*LD5S$5tc+@c(Y1Yn'
    'hD1jBMULrmL}TDg?bWmA+pEYvF#1DpvyLuj7xavMm07bB`6R;|ROZLte{k9iN9)sSk}>3kdsloETW7S1xPqt5>7lroT&G1fb4tD>'
    'wkd0WJhahWa12AZ9rmS478pwxIhdM1gk|P&_#&K3MdXRYlYpC#6$oF5R~XTv3dO)H6-FEr(`B`FxK=(wWpEy=e8E3F(}ee>$XG?d'
    'RqlqNTzxz?y(cSXsCYSDF@WIq3d4Fb%ElATwH_NIQA(HNupWUhtiLG(1Vg^lXhFlu%j(L4$a~A__=dI)xB7TgXh=qakToV`!y}6m'
    '^J6F_X(B4|p2_5`#cQhQP)^oT)agA_2c@J@@JQB6GA$QZ@b5~o7`1K%d9Xw?u#1`P)BRTEFH>+0eVkoeKX#>Q{`LwW?m~gvPVIPT'
    'x`r)Z);MsF!DcvQ|C@rfsItNldlS;^xXaIsuE$-*?AWV<ko*waGEG(r%FUWkXIX%Su_LVq)Md!;S)il|0;Sut4+@)D9WC<3??|h5'
    'dg*B|5?56)iAaSW+pdPC@tf*DQn0SF1Iy-skLO$TrmsWBGos=(p)XA5j4&>K_4tP){1p_%$xDnw8iNW3ozp|(t6Nb)@u5|bLJet`'
    '3TqcNOmkC)sq&|Du)#$cRnzpvf2-liyHJv!;CMIov8kV&7<_^=>YBiJ1$vKTm5(4RbI0=YlH1`uK0QOK&+_%x<!>>LOPD8d_ND`a'
    ')H4JI(+)KS228TZk{bgNVJvg7&|*Lx3PR`ca6C46CJZ~|&@Km7emV!s;Uo{~;AMyWC`p1E1MeFTQC!Hr@$kM$m=AuFAnVJx6VJ;K'
    'KST$dj;*+k`BxeVX(xU)qYI7<;+mE=#5SmlXLsX^Tn1+nHiW$1Vpy8)R(`n%S7Wk4v<GyTlA?iW$qES0%G=Q#RsU2Uofy7Z2-lO;'
    '7#8^+M$1D49~;;FrQ=$Uk;<H+Cam&H&G%}U$c0NqDvVE+Y)snQkQo^I08PI9Ty~ZOkCt3mQ4zs=Px@Ro;KB-e)@=vY`lg|eCS#30'
    'g)<Fr#g{G>v?@4jl95Toi%Lr5ejCrF<^_=ZY`DoJk!@g1#k<U2zR3iaN=&S~H()jJz~*lWURb60IB!4Tkf%9V|GCSD9F<3?tv{QC'
    'd@wJ~3Kw{tfY#ZscB4fFn@|p;J8Ju3quXe=8m$M#l(LTJBZQ|aiqDI4(}=pNw$<XiQiioKVz=I`Q~5)2ft5)_l=-C9e$*;1PP27;'
    '?M{c-($j1Vhx#wYu~k*pTLz<<^)Rm1KIot>$e7*wmlWKXk>pWcgO_XV)?VYmp<3<XbZFJxgGQGohPvoQ=kaBDVK#ZE-rD6;x&<%J'
    'CZ&Ru4rw`eIjvg%QN2dA^ILzM^6E)sa>c7BLE6J^ay<!0`zi+NNjR$6uI@60rZ9WeMw7b7kh~PebPqdqwOc2?g{_~YsJbmspuu)t'
    'U47W7)#KHpl?~(f>(yp&pK5$)Wy84L`U6#{h>A=JmaOwz{}{Lg#WiyaZs5o}?M91=gt~{#Xx-6Ea4$;GYHRHs>ZZP{)jzB4S6dJ2'
    'd~KtY`@LR=es|g}c6@5}#zUs(9k>~#6%)8yZ#CGQ#)zw_IHRpzquN9zs}FJZt2@m)o-LfuC@mF+iS=2ex~M6<TE%g;)t;nEQzh2a'
    'Ty(lUJoN|uUR%2VcZ-m1<1OB$TU@C@nwRP*kwTVGMNfGn9x1%0CV-z@R&GTe`J>Mf<@ovzPuiDIj20Lx315#VRJ@lHJ|au3xOP}D'
    '7Ly%UUP&5E%qu|o)n_9Xo&FXqha*7c?iBxYFk8Min@9-Pp%@K8lt}eOLiAOB@iH>e#Tc%|I@3y|6lhjtQhw!`XnBvaBJTeHdasWs'
)
DESCRIPTORS = descriptor_pb2.FileDescriptorSet.FromString(zlib.decompress(base64.b85decode(_DATA)))
_POOL = descriptor_pool.DescriptorPool()
for _file in DESCRIPTORS.file:
    _POOL.Add(_file)
_SERVICE = _POOL.FindServiceByName('openshell.v1.OpenShell')
METHODS = {method.name: (message_factory.GetMessageClass(method.input_type),
                         message_factory.GetMessageClass(method.output_type))
           for method in _SERVICE.methods}


def message(type_name, **fields):
    return message_factory.GetMessageClass(_POOL.FindMessageTypeByName(type_name))(**fields)
